"""
Module containing routines used by 3D datacubes.

.. include:: ../include/links.rst
"""

import os

from astropy import wcs, units
from astropy.coordinates import AltAz, SkyCoord
from astropy.io import fits
import scipy.optimize as opt
from scipy.interpolate import interp1d
import numpy as np

from pypeit import msgs
from pypeit import utils
from pypeit.core import coadd, flux_calib

# Use a fast histogram for speed!
from fast_histogram import histogramdd

from IPython import embed


def gaussian2D(tup, intflux, xo, yo, sigma_x, sigma_y, theta, offset):
    """
    Fit a 2D Gaussian function to an image.

    Args:
        tup (:obj:`tuple`):
            A two element tuple containing the x and y coordinates of each pixel
            in the image
        intflux (float):
            The Integrated flux of the 2D Gaussian
        xo (float):
            The centre of the Gaussian along the x-coordinate when z=0
        yo (float):
            The centre of the Gaussian along the y-coordinate when z=0
        sigma_x (float):
            The standard deviation in the x-direction
        sigma_y (float):
            The standard deviation in the y-direction
        theta (float):
            The orientation angle of the 2D Gaussian
        offset (float):
            Constant offset

    Returns:
        `numpy.ndarray`_: The 2D Gaussian evaluated at the coordinate (x, y)
    """
    # Extract the (x, y, z) coordinates of each pixel from the tuple
    (x, y) = tup
    # Ensure these are floating point
    xo = float(xo)
    yo = float(yo)
    # Account for a rotated 2D Gaussian
    a = (np.cos(theta)**2)/(2*sigma_x**2) + (np.sin(theta)**2)/(2*sigma_y**2)
    b = -(np.sin(2*theta))/(4*sigma_x**2) + (np.sin(2*theta))/(4*sigma_y**2)
    c = (np.sin(theta)**2)/(2*sigma_x**2) + (np.cos(theta)**2)/(2*sigma_y**2)
    # Normalise so that the integrated flux is a parameter, instead of the amplitude
    norm = 1/(2*np.pi*np.sqrt(a*c-b*b))
    gtwod = offset + norm*intflux*np.exp(-(a*((x-xo)**2) + 2*b*(x-xo)*(y-yo) + c*((y-yo)**2)))
    return gtwod.ravel()


def fitGaussian2D(image, norm=False):
    """
    Fit a 2D Gaussian to an input image. It is recommended that the input image
    is scaled to a maximum value that is ~1, so that all fit parameters are of
    the same order of magnitude. Set norm=True if you do not care about the
    amplitude or integrated flux. Otherwise, make sure you scale the image by
    a known value prior to passing it into this function.

    Parameters
    ----------
    image : `numpy.ndarray`_
        A 2D input image
    norm : bool, optional
        If True, the input image will be normalised to the maximum value
        of the input image.

    Returns
    -------
    popt : `numpy.ndarray`_
       The optimum parameters of the Gaussian in the following order: Integrated
       flux, x center, y center, sigma_x, sigma_y, theta, offset. See
       :func:`~pypeit.core.datacube.gaussian2D` for a more detailed description
       of the model.
    pcov : `numpy.ndarray`_
        Corresponding covariance matrix
    """
    # Normalise if requested
    wlscl = np.max(image) if norm else 1
    # Setup the coordinates
    x = np.linspace(0, image.shape[0] - 1, image.shape[0])
    y = np.linspace(0, image.shape[1] - 1, image.shape[1])
    xx, yy = np.meshgrid(x, y, indexing='ij')
    # Setup the fitting params
    idx_max = [image.shape[0]/2, image.shape[1]/2]  # Just use the centre of the image as the best guess
    #idx_max = np.unravel_index(np.argmax(image), image.shape)
    initial_guess = (1, idx_max[0], idx_max[1], 2, 2, 0, 0)
    bounds = ([0, 0, 0, 0.5, 0.5, -np.pi, -np.inf],
              [np.inf, image.shape[0], image.shape[1], image.shape[0], image.shape[1], np.pi, np.inf])
    # Perform the fit
    popt, pcov = opt.curve_fit(gaussian2D, (xx, yy), image.ravel() / wlscl, bounds=bounds, p0=initial_guess)
    # Return the fitting results
    return popt, pcov


def dar_fitfunc(radec, coord_ra, coord_dec, datfit, wave, obstime, location, pressure,
                temperature, rel_humidity):
    """
    Generates a fitting function to calculate the offset due to differential
    atmospheric refraction

    Args:
        radec (tuple):
            A tuple containing two floats representing the shift in ra and dec
            due to DAR.
        coord_ra (float):
            RA in degrees
        coord_dec (float):
            Dec in degrees
        datfit (`numpy.ndarray`_):
            The RA and DEC that the model needs to match
        wave (float):
            Wavelength to calculate the DAR
        location (`astropy.coordinates.EarthLocation`_):
            observatory location
        pressure (float):
            Outside pressure at `location`
        temperature (float):
            Outside ambient air temperature at `location`
        rel_humidity (float):
            Outside relative humidity at `location`. This should be between 0 to 1.

    Returns:
        float: chi-squared difference between datfit and model
    """
    (diff_ra, diff_dec) = radec
    # Generate the coordinate with atmospheric conditions
    coord_atmo = SkyCoord(coord_ra + diff_ra, coord_dec + diff_dec, unit=(units.deg, units.deg))
    coord_altaz = coord_atmo.transform_to(AltAz(obstime=obstime, location=location, obswl=wave,
                                          pressure=pressure, temperature=temperature,
                                          relative_humidity=rel_humidity))
    # Return chi-squared value
    return np.sum((np.array([coord_altaz.alt.value, coord_altaz.az.value])-datfit)**2)


def correct_grating_shift(wave_eval, wave_curr, spl_curr, wave_ref, spl_ref, order=2):
    """
    Using spline representations of the blaze profile, calculate the grating
    correction that should be applied to the current spectrum (suffix ``curr``)
    relative to the reference spectrum (suffix ``ref``). The grating correction
    is then evaluated at the wavelength array given by ``wave_eval``.

    Args:
        wave_eval (`numpy.ndarray`_):
            Wavelength array to evaluate the grating correction
        wave_curr (`numpy.ndarray`_):
            Wavelength array used to construct spl_curr
        spl_curr (`scipy.interpolate.interp1d`_):
            Spline representation of the current blaze function (based on the illumflat).
        wave_ref (`numpy.ndarray`_):
            Wavelength array used to construct spl_ref
        spl_ref (`scipy.interpolate.interp1d`_):
            Spline representation of the reference blaze function (based on the illumflat).
        order (int):
            Polynomial order used to fit the grating correction.

    Returns:
        `numpy.ndarray`_: The grating correction to apply
    """
    msgs.info("Calculating the grating correction")
    # Calculate the grating correction
    grat_corr_tmp = spl_curr(wave_eval) / spl_ref(wave_eval)
    # Determine the useful overlapping wavelength range
    minw, maxw = max(np.min(wave_curr), np.min(wave_ref)), max(np.min(wave_curr), np.max(wave_ref))
    # Perform a low-order polynomial fit to the grating correction (should be close to linear)
    wave_corr = (wave_eval - minw) / (maxw - minw)  # Scale wavelengths to be of order 0-1
    wblz = np.where((wave_corr > 0.1) & (wave_corr < 0.9))  # Remove the pixels that are within 10% of the edges
    coeff_gratcorr = np.polyfit(wave_corr[wblz], grat_corr_tmp[wblz], order)
    grat_corr = np.polyval(coeff_gratcorr, wave_corr)
    # Return the estimates grating correction
    return grat_corr


def extract_standard_spec(stdcube, subpixel=20):
    """
    Extract a spectrum of a standard star from a datacube

    Parameters
    ----------
    std_cube : `astropy.io.fits.HDUList`_
        An HDU list of fits files
    subpixel : int
        Number of pixels to subpixelate spectrum when creating mask

    Returns
    -------
    wave : `numpy.ndarray`_
        Wavelength of the star.
    Nlam_star : `numpy.ndarray`_
        counts/second/Angstrom
    Nlam_ivar_star : `numpy.ndarray`_
        inverse variance of Nlam_star
    gpm_star : `numpy.ndarray`_
        good pixel mask for Nlam_star
    """
    # Extract some information from the HDU list
    flxcube = stdcube['FLUX'].data.T.copy()
    varcube = stdcube['SIG'].data.T.copy()**2
    bpmcube = stdcube['BPM'].data.T.copy()
    numwave = flxcube.shape[2]

    # Setup the WCS
    stdwcs = wcs.WCS(stdcube['FLUX'].header)

    wcs_scale = (1.0 * stdwcs.spectral.wcs.cunit[0]).to(units.Angstrom).value  # Ensures the WCS is in Angstroms
    wave = wcs_scale * stdwcs.spectral.wcs_pix2world(np.arange(numwave), 0)[0]

    # Generate a whitelight image, and fit a 2D Gaussian to estimate centroid and width
    wl_img = make_whitelight_fromcube(flxcube)
    popt, pcov = fitGaussian2D(wl_img, norm=True)
    wid = max(popt[3], popt[4])

    # Setup the coordinates of the mask
    x = np.linspace(0, flxcube.shape[0] - 1, flxcube.shape[0] * subpixel)
    y = np.linspace(0, flxcube.shape[1] - 1, flxcube.shape[1] * subpixel)
    xx, yy = np.meshgrid(x, y, indexing='ij')

    # Generate a mask
    newshape = (flxcube.shape[0] * subpixel, flxcube.shape[1] * subpixel)
    mask = np.zeros(newshape)
    nsig = 4  # 4 sigma should be far enough... Note: percentage enclosed for 2D Gaussian = 1-np.exp(-0.5 * nsig**2)
    ww = np.where((np.sqrt((xx - popt[1]) ** 2 + (yy - popt[2]) ** 2) < nsig * wid))
    mask[ww] = 1
    mask = utils.rebinND(mask, (flxcube.shape[0], flxcube.shape[1])).reshape(flxcube.shape[0], flxcube.shape[1], 1)

    # Generate a sky mask
    newshape = (flxcube.shape[0] * subpixel, flxcube.shape[1] * subpixel)
    smask = np.zeros(newshape)
    nsig = 8  # 8 sigma should be far enough
    ww = np.where((np.sqrt((xx - popt[1]) ** 2 + (yy - popt[2]) ** 2) < nsig * wid))
    smask[ww] = 1
    smask = utils.rebinND(smask, (flxcube.shape[0], flxcube.shape[1])).reshape(flxcube.shape[0], flxcube.shape[1], 1)
    smask -= mask

    # Subtract the residual sky
    skymask = np.logical_not(bpmcube) * smask
    skycube = flxcube * skymask
    skyspec = skycube.sum(0).sum(0)
    nrmsky = skymask.sum(0).sum(0)
    skyspec *= utils.inverse(nrmsky)
    flxcube -= skyspec.reshape((1, 1, numwave))

    # Subtract the residual sky from the whitelight image
    sky_val = np.sum(wl_img[:, :, np.newaxis] * smask) / np.sum(smask)
    wl_img -= sky_val

    msgs.info("Extracting a boxcar spectrum of datacube")
    # Construct an image that contains the fraction of flux included in the
    # boxcar extraction at each wavelength interval
    norm_flux = wl_img[:,:,np.newaxis] * mask
    norm_flux /= np.sum(norm_flux)
    # Extract boxcar
    cntmask = np.logical_not(bpmcube) * mask  # Good pixels within the masked region around the standard star
    flxscl = (norm_flux * cntmask).sum(0).sum(0)  # This accounts for the flux that is missing due to masked pixels
    scimask = flxcube * cntmask
    varmask = varcube * cntmask**2
    nrmcnt = utils.inverse(flxscl)
    box_flux = scimask.sum(0).sum(0) * nrmcnt
    box_var = varmask.sum(0).sum(0) * nrmcnt**2
    box_gpm = flxscl > 1/3  # Good pixels are those where at least one-third of the standard star flux is measured
    # Setup the return values
    ret_flux, ret_var, ret_gpm = box_flux, box_var, box_gpm

    # Convert from counts/s/Ang/arcsec**2 to counts/s/Ang
    arcsecSQ = 3600.0*3600.0*(stdwcs.wcs.cdelt[0]*stdwcs.wcs.cdelt[1])
    ret_flux *= arcsecSQ
    ret_var *= arcsecSQ**2
    # Return the box extraction results
    return wave, ret_flux, utils.inverse(ret_var), ret_gpm


def make_sensfunc(ss_file, senspar, blaze_wave=None, blaze_spline=None, grating_corr=False):
    """
    Generate the sensitivity function from a standard star DataCube.

    Args:
        ss_file (:obj:`str`_):
            The relative path and filename of the standard star datacube. It should be fits format, and
            for full functionality, should ideally of the form `pypeit.coadd3d.DataCube`_
        senspar (:class:`~pypeit.par.pypeitpar.SensFuncPar`):
            The parameters required for the sensitivity function computation.
        blaze_wave (`numpy.ndarray`_, optional):
            Wavelength array used to construct blaze_spline
        blaze_spline (`scipy.interpolate.interp1d`_, optional):
            Spline representation of the reference blaze function (based on the illumflat).
        grating_corr (:obj:`bool`_, optional):
            If a grating correction should be performed, set this variable to True.

    Returns:
        `numpy.ndarray`_: A mask of the good sky pixels (True = good)
    """
    # Check if the standard star datacube exists
    if not os.path.exists(ss_file):
        msgs.error("Standard cube does not exist:" + msgs.newline() + ss_file)
    msgs.info(f"Loading standard star cube: {ss_file:s}")
    # Load the standard star cube and retrieve its RA + DEC
    stdcube = fits.open(ss_file)
    star_ra, star_dec = stdcube[1].header['CRVAL1'], stdcube[1].header['CRVAL2']

    # Extract a spectrum of the standard star
    wave, Nlam_star, Nlam_ivar_star, gpm_star = extract_standard_spec(stdcube)

    # Extract the information about the blaze
    if grating_corr:
        blaze_wave_curr, blaze_spec_curr = stdcube['BLAZE_WAVE'].data, stdcube['BLAZE_SPEC'].data
        blaze_spline_curr = interp1d(blaze_wave_curr, blaze_spec_curr,
                                     kind='linear', bounds_error=False, fill_value="extrapolate")
        # Perform a grating correction
        grat_corr = correct_grating_shift(wave, blaze_wave_curr, blaze_spline_curr, blaze_wave, blaze_spline)
        # Apply the grating correction to the standard star spectrum
        Nlam_star /= grat_corr
        Nlam_ivar_star *= grat_corr ** 2

    # Read in some information above the standard star
    std_dict = flux_calib.get_standard_spectrum(star_type=senspar['star_type'],
                                                star_mag=senspar['star_mag'],
                                                ra=star_ra, dec=star_dec)
    # Calculate the sensitivity curve
    # TODO :: This needs to be addressed... unify flux calibration into the main PypeIt routines.
    msgs.warn("Datacubes are currently flux-calibrated using the UVIS algorithm... this will be deprecated soon")
    zeropoint_data, zeropoint_data_gpm, zeropoint_fit, zeropoint_fit_gpm = \
        flux_calib.fit_zeropoint(wave, Nlam_star, Nlam_ivar_star, gpm_star, std_dict,
                                 mask_hydrogen_lines=senspar['mask_hydrogen_lines'],
                                 mask_helium_lines=senspar['mask_helium_lines'],
                                 hydrogen_mask_wid=senspar['hydrogen_mask_wid'],
                                 nresln=senspar['UVIS']['nresln'],
                                 resolution=senspar['UVIS']['resolution'],
                                 trans_thresh=senspar['UVIS']['trans_thresh'],
                                 polyorder=senspar['polyorder'],
                                 polycorrect=senspar['UVIS']['polycorrect'],
                                 polyfunc=senspar['UVIS']['polyfunc'])
    wgd = np.where(zeropoint_fit_gpm)
    sens = np.power(10.0, -0.4 * (zeropoint_fit[wgd] - flux_calib.ZP_UNIT_CONST)) / np.square(wave[wgd])
    return interp1d(wave[wgd], sens, kind='linear', bounds_error=False, fill_value="extrapolate")


def make_good_skymask(slitimg, tilts):
    """
    Mask the spectral edges of each slit (i.e. the pixels near the ends of the
    detector in the spectral direction). Some extreme values of the tilts are
    only sampled with a small fraction of the pixels of the slit width. This
    leads to a bad extrapolation/determination of the sky model.

    Args:
        slitimg (`numpy.ndarray`_):
            An image of the slit indicating which slit each pixel belongs to
        tilts (`numpy.ndarray`_):
            Spectral tilts.

    Returns:
        `numpy.ndarray`_: A mask of the good sky pixels (True = good)
    """
    msgs.info("Masking edge pixels where the sky model is poor")
    # Initialise the GPM
    gpm = np.zeros(slitimg.shape, dtype=bool)
    # Find unique slits
    unq = np.unique(slitimg[slitimg>0])
    for uu in range(unq.size):
        # Find the x,y pixels in this slit
        ww = np.where((slitimg == unq[uu]) & (tilts != 0.0))
        # Mask the bottom pixels first
        wb = np.where(ww[0] == 0)[0]
        wt = np.where(ww[0] == np.max(ww[0]))[0]
        # Calculate the maximum tilt from the bottom row, and the miminum tilt from the top row
        maxtlt = np.max(tilts[0,  ww[1][wb]])
        mintlt = np.min(tilts[-1, ww[1][wt]])
        # Mask all values below this maximum
        gpm[ww] = (tilts[ww] >= maxtlt) & (tilts[ww] <= mintlt)  # The signs are correct here.
    return gpm


def get_output_filename(fil, par_outfile, combine, idx=1):
    """
    Get the output filename of a datacube, given the input

    Args:
        fil (str):
            The spec2d filename.
        par_outfile (str):
            The user-specified output filename (see cubepar['output_filename'])
        combine (bool):
            Should the input frames be combined into a single datacube?
        idx (int, optional):
            Index of filename to be saved. Required if combine=False.

    Returns:
        str: The output filename to use.
    """
    if combine:
        if par_outfile == '':
            par_outfile = 'datacube.fits'
        # Check if we needs to append an extension
        return par_outfile if '.fits' in par_outfile else f'{par_outfile}.fits'
    if par_outfile == '':
        return fil.replace('spec2d_', 'spec3d_')
    # Finally, if nothing else, use the output filename as a prefix, and a numerical suffic
    return os.path.splitext(par_outfile)[0] + f'_{idx:03}.fits'


def get_output_whitelight_filename(outfile):
    """
    Given the output filename of a datacube, create an appropriate whitelight
    fits file name

    Args:
        outfile (str):
            The output filename used for the datacube.

    Returns:
        A string containing the output filename to use for the whitelight image.
    """
    return os.path.splitext(outfile)[0] + "_whitelight.fits"


def get_whitelight_pixels(all_wave, min_wl, max_wl):
    """
    Determine which pixels are included within the specified wavelength range

    Args:
        all_wave (`numpy.ndarray`_):
            The wavelength of each individual pixel
        min_wl (float):
            Minimum wavelength to consider
        max_wl (float):
            Maximum wavelength to consider

    Returns:
        :obj:`tuple`: A `numpy.ndarray`_ object with the indices of all_wave
        that contain pixels within the requested wavelength range, and a float
        with the wavelength range (i.e. maximum wavelength - minimum wavelength)
    """
    wavediff = np.max(all_wave) - np.min(all_wave)
    if min_wl < max_wl:
        ww = np.where((all_wave > min_wl) & (all_wave < max_wl))
        wavediff = max_wl - min_wl
    else:
        msgs.warn("Datacubes do not completely overlap in wavelength. Offsets may be unreliable...")
        ww = (np.arange(all_wave.size),)
    return ww, wavediff


def get_whitelight_range(wavemin, wavemax, wl_range):
    """
    Get the wavelength range to use for the white light images

    Parameters
    ----------
    wavemin : float
        Automatically determined minimum wavelength to use for making the white
        light image.
    wavemax : float
        Automatically determined maximum wavelength to use for making the white
        light image.
    wl_range : list
        Two element list containing the user-specified values to manually
        override the automated values determined by PypeIt.

    Returns
    -------
    wlrng : list
        A two element list containing the minimum and maximum wavelength to use
        for the white light images
    """
    wlrng = [wavemin, wavemax]
    if wl_range[0] is not None:
        if wl_range[0] < wavemin:
            msgs.warn("The user-specified minimum wavelength ({0:.2f}) to use for the white light".format(wl_range[0]) +
                      msgs.newline() + "images is lower than the recommended value ({0:.2f}),".format(wavemin) +
                      msgs.newline() + "which ensures that all spaxels cover the same wavelength range.")
        wlrng[0] = wl_range[0]
    if wl_range[1] is not None:
        if wl_range[1] > wavemax:
            msgs.warn("The user-specified maximum wavelength ({0:.2f}) to use for the white light".format(wl_range[1]) +
                      msgs.newline() + "images is greater than the recommended value ({0:.2f}),".format(wavemax) +
                      msgs.newline() + "which ensures that all spaxels cover the same wavelength range.")
        wlrng[1] = wl_range[1]
    msgs.info("The white light images will cover the wavelength range: {0:.2f}A - {1:.2f}A".format(wlrng[0], wlrng[1]))
    return wlrng


def make_whitelight_fromcube(cube, wave=None, wavemin=None, wavemax=None):
    """
    Generate a white light image using an input cube.

    Args:
        cube (`numpy.ndarray`_):
            3D datacube (the final element contains the wavelength dimension)
        wave (`numpy.ndarray`_, optional):
            1D wavelength array. Only required if wavemin or wavemax are not
            None.
        wavemin (float, optional):
            Minimum wavelength (same units as wave) to be included in the
            whitelight image.  You must provide wave as well if you want to
            reduce the wavelength range.
        wavemax (float, optional):
            Maximum wavelength (same units as wave) to be included in the
            whitelight image.  You must provide wave as well if you want to
            reduce the wavelength range.

    Returns:
        A whitelight image of the input cube (of type `numpy.ndarray`_).
    """
    # Make a wavelength cut, if requested
    cutcube = cube.copy()
    if wavemin is not None or wavemax is not None:
        # Make some checks on the input
        if wave is None:
            msgs.error("wave variable must be supplied to create white light image with wavelength cuts")
        else:
            if wave.size != cube.shape[2]:
                msgs.error("wave variable should have the same length as the third axis of cube.")
        # assign wavemin & wavemax if one is not provided
        if wavemin is None:
            wavemin = np.min(wave)
        if wavemax is None:
            wavemax = np.max(wave)
        ww = np.where((wave >= wavemin) & (wave <= wavemax))[0]
        wmin, wmax = ww[0], ww[-1]+1
        cutcube = cube[:, :, wmin:wmax]
    # Now sum along the wavelength axis
    nrmval = np.sum(cutcube != 0.0, axis=2)
    nrmval[nrmval == 0.0] = 1.0
    wl_img = np.sum(cutcube, axis=2) / nrmval
    return wl_img


def load_imageWCS(filename, ext=0):
    """
    Load an image and return the image and the associated WCS.

    Args:
        filename (str):
            A fits filename of an image to be used when generating white light
            images. Note, the fits file must have a valid 3D WCS.
        ext (bool, optional):
            The extension that contains the image and WCS

    Returns:
        :obj:`tuple`: An `numpy.ndarray`_ with the 2D image data and a
        `astropy.wcs.WCS`_ with the image WCS.
    """
    imghdu = fits.open(filename)
    image = imghdu[ext].data.T
    imgwcs = wcs.WCS(imghdu[ext].header)
    # Return required info
    return image, imgwcs


def align_user_offsets(all_ra, all_dec, all_idx, ifu_ra, ifu_dec, ra_offset, dec_offset):
    """
    Align the RA and DEC of all input frames, and then
    manually shift the cubes based on user-provided offsets.
    The offsets should be specified in arcseconds, and the
    ra_offset should include the cos(dec) factor.

    Args:
        all_ra (`numpy.ndarray`_):
            A 1D array containing the RA values of each detector pixel of every frame.
        all_dec (`numpy.ndarray`_):
            A 1D array containing the Dec values of each detector pixel of every frame.
            Same size as all_ra.
        all_idx (`numpy.ndarray`_):
            A 1D array containing an ID value for each detector frame (0-indexed).
            Same size as all_ra.
        ifu_ra (`numpy.ndarray`_):
            A list of RA values of the IFU (one value per frame)
        ifu_dec (`numpy.ndarray`_):
            A list of Dec values of the IFU (one value per frame)
        ra_offset (`numpy.ndarray`_):
            A list of RA offsets to be applied to the input pixel values (one value per frame).
            Note, the ra_offset MUST contain the cos(dec) factor. This is the number of arcseconds
            on the sky that represents the telescope offset.
        dec_offset (`numpy.ndarray`_):
            A list of Dec offsets to be applied to the input pixel values (one value per frame).

    Returns:
        A tuple containing a new set of RA and Dec values that have been aligned. Both arrays
        are of type `numpy.ndarray`_.
    """
    # First, translate all coordinates to the coordinates of the first frame
    # Note: You do not need cos(dec) here, this just overrides the IFU coordinate centre of each frame
    #       The cos(dec) factor should be input by the user, and should be included in the self.opts['ra_offset']
    ref_shift_ra = ifu_ra[0] - ifu_ra
    ref_shift_dec = ifu_dec[0] - ifu_dec
    numfiles = ra_offset.size
    for ff in range(numfiles):
        # Apply the shift
        all_ra[all_idx == ff] += ref_shift_ra[ff] + ra_offset[ff] / 3600.0
        all_dec[all_idx == ff] += ref_shift_dec[ff] + dec_offset[ff] / 3600.0
        msgs.info("Spatial shift of cube #{0:d}:".format(ff + 1) + msgs.newline() +
                  "RA, DEC (arcsec) = {0:+0.3f} E, {1:+0.3f} N".format(ra_offset[ff], dec_offset[ff]))
    return all_ra, all_dec


def set_voxel_sampling(spatscale, specscale, dspat=None, dwv=None):
    """
    This function checks if the spatial and spectral scales of all frames are consistent.
    If the user has not specified either the spatial or spectral scales, they will be set here.

    Parameters
    ----------
    spatscale : `numpy.ndarray`_
        2D array, shape is (N, 2), listing the native spatial scales of N spec2d frames.
        spatscale[:,0] refers to the spatial pixel scale of each frame
        spatscale[:,1] refers to the slicer scale of each frame
        Each element of the array must be in degrees
    specscale : `numpy.ndarray`_
        1D array listing the native spectral scales of multiple frames. The length of this array should be equal
        to the number of frames you are using. Each element of the array must be in Angstrom
    dspat: :obj:`float`, optional
        Spatial scale to use as the voxel spatial sampling. If None, a new value will be derived based on the inputs
    dwv: :obj:`float`, optional
        Spectral scale to use as the voxel spectral sampling. If None, a new value will be derived based on the inputs

    Returns
    -------
    _dspat : :obj:`float`
        Spatial sampling
    _dwv : :obj:`float`
        Wavelength sampling
    """
    # Make sure all frames have consistent pixel scales
    ratio = (spatscale[:, 0] - spatscale[0, 0]) / spatscale[0, 0]
    if np.any(np.abs(ratio) > 1E-4):
        msgs.warn("The pixel scales of all input frames are not the same!")
        spatstr = ", ".join(["{0:.6f}".format(ss) for ss in spatscale[:,0]*3600.0])
        msgs.info("Pixel scales of all input frames:" + msgs.newline() + spatstr + "arcseconds")
    # Make sure all frames have consistent slicer scales
    ratio = (spatscale[:, 1] - spatscale[0, 1]) / spatscale[0, 1]
    if np.any(np.abs(ratio) > 1E-4):
        msgs.warn("The slicer scales of all input frames are not the same!")
        spatstr = ", ".join(["{0:.6f}".format(ss) for ss in spatscale[:,1]*3600.0])
        msgs.info("Slicer scales of all input frames:" + msgs.newline() + spatstr + "arcseconds")
    # Make sure all frames have consistent wavelength sampling
    ratio = (specscale - specscale[0]) / specscale[0]
    if np.any(np.abs(ratio) > 1E-2):
        msgs.warn("The wavelength samplings of the input frames are not the same!")
        specstr = ", ".join(["{0:.6f}".format(ss) for ss in specscale])
        msgs.info("Wavelength samplings of all input frames:" + msgs.newline() + specstr + "Angstrom")

    # If the user has not specified the spatial scale, then set it appropriately now to the largest spatial scale
    _dspat = np.max(spatscale) if dspat is None else dspat
    msgs.info("Adopting a square pixel spatial scale of {0:f} arcsec".format(3600.0 * _dspat))
    # If the user has not specified the spectral sampling, then set it now to the largest value
    _dwv = np.max(specscale) if dwv is None else dwv
    msgs.info("Adopting a wavelength sampling of {0:f} Angstrom".format(_dwv))
    return _dspat, _dwv


def wcs_bounds(all_ra, all_dec, all_wave, ra_min=None, ra_max=None, dec_min=None, dec_max=None, wave_min=None, wave_max=None):
    """
    Calculate the bounds of the WCS and the expected edges of the voxels, based on user-specified
    parameters or the extremities of the data. This is a convenience function
    that calls the core function in `pypeit.core.datacube`_.

    Parameters
    ----------
    all_ra : `numpy.ndarray`_
        1D flattened array containing the RA values of each pixel from all
        spec2d files
    all_dec : `numpy.ndarray`_
        1D flattened array containing the DEC values of each pixel from all
        spec2d files
    all_wave : `numpy.ndarray`_
        1D flattened array containing the wavelength values of each pixel from
        all spec2d files
    ra_min : :obj:`float`, optional
        Minimum RA of the WCS
    ra_max : :obj:`float`, optional
        Maximum RA of the WCS
    dec_min : :obj:`float`, optional
        Minimum Dec of the WCS
    dec_max : :obj:`float`, optional
        Maximum RA of the WCS
    wav_min : :obj:`float`, optional
        Minimum wavelength of the WCS
    wav_max : :obj:`float`, optional
        Maximum RA of the WCS

    Returns
    -------
    _ra_min : :obj:`float`
        Minimum RA of the WCS
    _ra_max : :obj:`float`
        Maximum RA of the WCS
    _dec_min : :obj:`float`
        Minimum Dec of the WCS
    _dec_max : :obj:`float`
        Maximum RA of the WCS
    _wav_min : :obj:`float`
        Minimum wavelength of the WCS
    _wav_max : :obj:`float`
        Maximum RA of the WCS
    """
    # Setup the cube ranges
    _ra_min = ra_min if ra_min is not None else np.min(all_ra)
    _ra_max = ra_max if ra_max is not None else np.max(all_ra)
    _dec_min = dec_min if dec_min is not None else np.min(all_dec)
    _dec_max = dec_max if dec_max is not None else np.max(all_dec)
    _wav_min = wave_min if wave_min is not None else np.min(all_wave)
    _wav_max = wave_max if wave_max is not None else np.max(all_wave)
    return _ra_min, _ra_max, _dec_min, _dec_max, _wav_min, _wav_max


def create_wcs(all_ra, all_dec, all_wave, dspat, dwave,
               ra_min=None, ra_max=None, dec_min=None, dec_max=None, wave_min=None, wave_max=None,
               reference=None, collapse=False, equinox=2000.0, specname="PYP_SPEC"):
    """
    Create a WCS and the expected edges of the voxels, based on user-specified
    parameters or the extremities of the data.

    Parameters
    ----------
    all_ra : `numpy.ndarray`_
        1D flattened array containing the RA values of each pixel from all
        spec2d files
    all_dec : `numpy.ndarray`_
        1D flattened array containing the DEC values of each pixel from all
        spec2d files
    all_wave : `numpy.ndarray`_
        1D flattened array containing the wavelength values of each pixel from
        all spec2d files
    dspat : float
        Spatial size of each square voxel (in arcsec). The default is to use the
        values in cubepar.
    dwave : float
        Linear wavelength step of each voxel (in Angstroms)
    ra_min : float, optional
        Minimum RA of the WCS (degrees)
    ra_max : float, optional
        Maximum RA of the WCS (degrees)
    dec_min : float, optional
        Minimum Dec of the WCS (degrees)
    dec_max : float, optional
        Maximum Dec of the WCS (degrees)
    wave_min : float, optional
        Minimum wavelength of the WCS (degrees)
    wave_max : float, optional
        Maximum wavelength of the WCS (degrees)
    reference : str, optional
        Filename of a fits file that contains a WCS in the Primary HDU.
    collapse : bool, optional
        If True, the spectral dimension will be collapsed to a single channel
        (primarily for white light images)
    equinox : float, optional
        Equinox of the WCS
    specname : str, optional
        Name of the spectrograph

    Returns
    -------
    cubewcs : `astropy.wcs.WCS`_
        astropy WCS to be used for the combined cube
    voxedges : tuple
        A three element tuple containing the bin edges in the x, y (spatial) and
        z (wavelength) dimensions
    reference_image : `numpy.ndarray`_
        The reference image to be used for the cross-correlation. Can be None.
    """
    # Grab cos(dec) for convenience
    cosdec = np.cos(np.mean(all_dec) * np.pi / 180.0)

    # Setup the cube ranges
    _ra_min, _ra_max, _dec_min, _dec_max, _wav_min, _wav_max = \
        wcs_bounds(all_ra, all_dec, all_wave, ra_min=ra_min, ra_max=ra_max, dec_min=dec_min, dec_max=dec_max,
                   wave_min=wave_min, wave_max=wave_max)

    # Number of voxels in each dimension
    numra = int((_ra_max - _ra_min) * cosdec / dspat)
    numdec = int((_dec_max - _dec_min) / dspat)
    numwav = int(np.round((_wav_max - _wav_min) / dwave))

    # If a white light WCS is being generated, make sure there's only 1 wavelength bin
    if collapse:
        _wav_min = np.min(all_wave)
        _wav_max = np.max(all_wave)
        dwave = _wav_max - _wav_min
        numwav = 1

    # Generate a master WCS to register all frames
    coord_min = [_ra_min, _dec_min, _wav_min]
    coord_dlt = [dspat, dspat, dwave]

    # If a reference image is being used and a white light image is requested (collapse=True) update the celestial parts
    reference_image = None
    if reference is not None:
        # Load the requested reference image
        reference_image, imgwcs = load_imageWCS(reference)
        # Update the celestial WCS
        coord_min[:2] = imgwcs.wcs.crval
        coord_dlt[:2] = imgwcs.wcs.cdelt
        numra, numdec = reference_image.shape

    cubewcs = generate_WCS(coord_min, coord_dlt, equinox=equinox, name=specname)
    msgs.info(msgs.newline() + "-" * 40 +
              msgs.newline() + "Parameters of the WCS:" +
              msgs.newline() + "RA   min = {0:f}".format(coord_min[0]) +
              msgs.newline() + "DEC  min = {0:f}".format(coord_min[1]) +
              msgs.newline() + "WAVE min, max = {0:f}, {1:f}".format(_wav_min, _wav_max) +
              msgs.newline() + "Spaxel size = {0:f} arcsec".format(3600.0 * dspat) +
              msgs.newline() + "Wavelength step = {0:f} A".format(dwave) +
              msgs.newline() + "-" * 40)

    # Generate the output binning
    xbins = np.arange(1 + numra) - 0.5
    ybins = np.arange(1 + numdec) - 0.5
    spec_bins = np.arange(1 + numwav) - 0.5
    voxedges = (xbins, ybins, spec_bins)
    return cubewcs, voxedges, reference_image


def generate_WCS(crval, cdelt, equinox=2000.0, name="PYP_SPEC"):
    """
    Generate a WCS that will cover all input spec2D files

    Args:
        crval (list):
            3 element list containing the [RA, DEC, WAVELENGTH] of
            the reference pixel
        cdelt (list):
            3 element list containing the delta values of the [RA,
            DEC, WAVELENGTH]
        equinox (float, optional):
            Equinox of the WCS

    Returns:
        `astropy.wcs.WCS`_ : astropy WCS to be used for the combined cube
    """
    # Create a new WCS object.
    msgs.info("Generating WCS")
    w = wcs.WCS(naxis=3)
    w.wcs.equinox = equinox
    w.wcs.name = name
    w.wcs.radesys = 'FK5'
    # Insert the coordinate frame
    w.wcs.cname = ['RA', 'DEC', 'Wavelength']
    w.wcs.cunit = [units.degree, units.degree, units.Angstrom]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN", "WAVE"]
    w.wcs.crval = crval  # RA, DEC, and wavelength zeropoints
    w.wcs.crpix = [0, 0, 0]  # RA, DEC, and wavelength reference pixels
    #w.wcs.cd = np.array([[cdval[0], 0.0, 0.0], [0.0, cdval[1], 0.0], [0.0, 0.0, cdval[2]]])
    w.wcs.cdelt = cdelt
    w.wcs.lonpole = 180.0  # Native longitude of the Celestial pole
    w.wcs.latpole = 0.0  # Native latitude of the Celestial pole
    return w


def compute_weights_frompix(all_ra, all_dec, all_wave, all_sci, all_ivar, all_idx, dspat, dwv, mnmx_wv, all_wghts,
                            all_spatpos, all_specpos, all_spatid, all_tilts, all_slits, all_align, all_dar,
                            ra_min=None, ra_max=None, dec_min=None, dec_max=None, wave_min=None, wave_max=None,
                            sn_smooth_npix=None, relative_weights=False, reference_image=None, whitelight_range=None,
                            specname="PYPSPEC"):
    r"""
    Calculate wavelength dependent optimal weights. The weighting is currently
    based on a relative :math:`(S/N)^2` at each wavelength. Note, this function
    first prepares a whitelight image, and then calls compute_weights() to
    determine the appropriate weights of each pixel.

    Args:
        all_ra (`numpy.ndarray`_):
            1D flattened array containing the RA values of each pixel from all
            spec2d files
        all_dec (`numpy.ndarray`_):
            1D flattened array containing the DEC values of each pixel from all
            spec2d files
        all_wave (`numpy.ndarray`_):
            1D flattened array containing the wavelength values of each pixel
            from all spec2d files
        all_sci (`numpy.ndarray`_):
            1D flattened array containing the counts of each pixel from all
            spec2d files
        all_ivar (`numpy.ndarray`_):
            1D flattened array containing the inverse variance of each pixel
            from all spec2d files
        all_idx (`numpy.ndarray`_):
            1D flattened array containing an integer identifier indicating which
            spec2d file each pixel originates from. For example, a 0 would
            indicate that a pixel originates from the first spec2d frame listed
            in the input file. a 1 would indicate that this pixel originates
            from the second spec2d file, and so forth.
        dspat (float):
            The size of each spaxel on the sky (in degrees)
        dwv (float):
            The size of each wavelength pixel (in Angstroms)
        mnmx_wv (`numpy.ndarray`_):
            The minimum and maximum wavelengths of every slit and frame. The shape is (Nframes, Nslits, 2),
            The minimum and maximum wavelengths are stored in the [:,:,0] and [:,:,1] indices, respectively.
        all_wghts (`numpy.ndarray`_):
            1D flattened array containing the weights of each pixel to be used
            in the combination
        all_spatpos (`numpy.ndarray`_):
            1D flattened array containing the detector pixel location in the
            spatial direction
        all_specpos (`numpy.ndarray`_):
            1D flattened array containing the detector pixel location in the
            spectral direction
        all_spatid (`numpy.ndarray`_):
            1D flattened array containing the spatid of each pixel
        tilts (`numpy.ndarray`_, list):
            2D wavelength tilts frame, or a list of tilt frames (see all_idx)
        slits (:class:`~pypeit.slittrace.SlitTraceSet`, list):
            Information stored about the slits, or a list of SlitTraceSet (see
            all_idx)
        astrom_trans (:class:`~pypeit.alignframe.AlignmentSplines`, list):
            A Class containing the transformation between detector pixel
            coordinates and WCS pixel coordinates, or a list of Alignment
            Splines (see all_idx)
        all_dar (:class:`~pypeit.coadd3d.DARcorrection`, list):
            A Class containing the DAR correction information, or a list of DARcorrection
            classes. If a list, it must be the same length as astrom_trans.
        ra_min (float, optional):
            Minimum RA of the WCS (degrees)
        ra_max (float, optional):
            Maximum RA of the WCS (degrees)
        dec_min (float, optional):
            Minimum Dec of the WCS (degrees)
        dec_max (float, optional):
            Maximum Dec of the WCS (degrees)
        wave_min (float, optional):
            Minimum wavelength of the WCS (degrees)
        wave_max (float, optional):
            Maximum wavelength of the WCS (degrees)
        sn_smooth_npix (float, optional):
            Number of pixels used for determining smoothly varying S/N ratio
            weights.  This is currently not required, since a relative weighting
            scheme with a polynomial fit is used to calculate the S/N weights.
        relative_weights (bool, optional):
            Calculate weights by fitting to the ratio of spectra?
        reference_image (`numpy.ndarray`_):
            Reference image to use for the determination of the highest S/N spaxel in the image.
        specname (str):
            Name of the spectrograph

    Returns:
        `numpy.ndarray`_ : a 1D array the same size as all_sci, containing
        relative wavelength dependent weights of each input pixel.
    """
    # Find the wavelength range where all frames overlap
    min_wl, max_wl = get_whitelight_range(np.max(mnmx_wv[:, :, 0]),  # The max blue wavelength
                                          np.min(mnmx_wv[:, :, 1]),  # The min red wavelength
                                          whitelight_range)  # The user-specified values (if any)
    # Get the good white light pixels
    ww, wavediff = get_whitelight_pixels(all_wave, min_wl, max_wl)

    # Generate the WCS
    image_wcs, voxedge, reference_image = \
        create_wcs(all_ra, all_dec, all_wave, dspat, wavediff,
                   ra_min=ra_min, ra_max=ra_max, dec_min=dec_min, dec_max=dec_max, wave_min=wave_min, wave_max=wave_max,
                   reference=reference_image, collapse=True, equinox=2000.0,
                   specname=specname)

    # Generate the white light image
    # NOTE: hard-coding subpixel=1 in both directions for speed, and combining into a single image
    wl_full = generate_image_subpixel(image_wcs, all_ra, all_dec, all_wave, all_sci, all_ivar, all_wghts,
                                      all_spatpos, all_specpos, all_spatid, all_tilts, all_slits, all_align, all_dar,
                                      voxedge, all_idx=all_idx, spec_subpixel=1, spat_subpixel=1, combine=True)
    # Compute the weights
    return compute_weights(all_ra, all_dec, all_wave, all_sci, all_ivar, all_idx, wl_full[:, :, 0], dspat, dwv,
                           sn_smooth_npix=sn_smooth_npix, relative_weights=relative_weights)


def compute_weights(all_ra, all_dec, all_wave, all_sci, all_ivar, all_idx, whitelight_img, dspat, dwv,
                    sn_smooth_npix=None, relative_weights=False):
    r"""
    Calculate wavelength dependent optimal weights. The weighting is currently
    based on a relative :math:`(S/N)^2` at each wavelength

    Args:
        all_ra (`numpy.ndarray`_):
            1D flattened array containing the RA values of each pixel from all
            spec2d files
        all_dec (`numpy.ndarray`_):
            1D flattened array containing the DEC values of each pixel from all
            spec2d files
        all_wave (`numpy.ndarray`_):
            1D flattened array containing the wavelength values of each pixel
            from all spec2d files
        all_sci (`numpy.ndarray`_):
            1D flattened array containing the counts of each pixel from all
            spec2d files
        all_ivar (`numpy.ndarray`_):
            1D flattened array containing the inverse variance of each pixel
            from all spec2d files
        all_idx (`numpy.ndarray`_):
            1D flattened array containing an integer identifier indicating which
            spec2d file each pixel originates from. For example, a 0 would
            indicate that a pixel originates from the first spec2d frame listed
            in the input file. a 1 would indicate that this pixel originates
            from the second spec2d file, and so forth.
        whitelight_img (`numpy.ndarray`_):
            A 2D array containing a whitelight image, that was created with the
            input ``all_`` arrays.
        dspat (float):
            The size of each spaxel on the sky (in degrees)
        dwv (float):
            The size of each wavelength pixel (in Angstroms)
        sn_smooth_npix (float, optional):
            Number of pixels used for determining smoothly varying S/N ratio
            weights.  This is currently not required, since a relative weighting
            scheme with a polynomial fit is used to calculate the S/N weights.
        relative_weights (bool, optional):
            Calculate weights by fitting to the ratio of spectra?

    Returns:
        `numpy.ndarray`_ : a 1D array the same size as all_sci, containing
        relative wavelength dependent weights of each input pixel.
    """
    msgs.info("Calculating the optimal weights of each pixel")
    # Determine number of files
    numfiles = np.unique(all_idx).size

    # Find the location of the object with the highest S/N in the combined white light image
    idx_max = np.unravel_index(np.argmax(whitelight_img), whitelight_img.shape)
    msgs.info("Highest S/N object located at spaxel (x, y) = {0:d}, {1:d}".format(idx_max[0], idx_max[1]))

    # Generate a 2D WCS to register all frames
    coord_min = [np.min(all_ra), np.min(all_dec), np.min(all_wave)]
    coord_dlt = [dspat, dspat, dwv]
    whitelightWCS = generate_WCS(coord_min, coord_dlt)
    # Make the bin edges to be at +/- 1 pixels around the maximum (i.e. summing 9 pixels total)
    numwav = int((np.max(all_wave) - np.min(all_wave)) / dwv)
    xbins = np.array([idx_max[0]-1, idx_max[0]+2]) - 0.5
    ybins = np.array([idx_max[1]-1, idx_max[1]+2]) - 0.5
    spec_bins = np.arange(1 + numwav) - 0.5
    bins = (xbins, ybins, spec_bins)

    # Extract the spectrum of the highest S/N object
    flux_stack = np.zeros((numwav, numfiles))
    ivar_stack = np.zeros((numwav, numfiles))
    for ff in range(numfiles):
        msgs.info("Extracting spectrum of highest S/N detection from frame {0:d}/{1:d}".format(ff + 1, numfiles))
        ww = (all_idx == ff)
        # Extract the spectrum
        pix_coord = whitelightWCS.wcs_world2pix(np.vstack((all_ra[ww], all_dec[ww], all_wave[ww] * 1.0E-10)).T, 0)
        spec, edges = np.histogramdd(pix_coord, bins=bins, weights=all_sci[ww])
        var, edges = np.histogramdd(pix_coord, bins=bins, weights=1/all_ivar[ww])
        norm, edges = np.histogramdd(pix_coord, bins=bins)
        normspec = (norm > 0) / (norm + (norm == 0))
        var_spec = var[0, 0, :]
        ivar_spec = (var_spec > 0) / (var_spec + (var_spec == 0))
        # Calculate the S/N in a given spectral bin
        flux_stack[:, ff] = spec[0, 0, :] * np.sqrt(normspec)  # Note: sqrt(nrmspec), is because we want the S/N in a _single_ pixel (i.e. not spectral bin)
        ivar_stack[:, ff] = ivar_spec

    mask_stack = (flux_stack != 0.0) & (ivar_stack != 0.0)
    # Obtain a wavelength of each pixel
    wcs_res = whitelightWCS.wcs_pix2world(np.vstack((np.zeros(numwav), np.zeros(numwav), np.arange(numwav))).T, 0)
    wcs_scale = (1.0 * whitelightWCS.wcs.cunit[2]).to_value(units.Angstrom)  # Ensures the WCS is in Angstroms
    wave_spec = wcs_scale * wcs_res[:, 2]
    # Compute the smoothing scale to use
    if sn_smooth_npix is None:
        sn_smooth_npix = int(np.round(0.1 * wave_spec.size))
    rms_sn, weights = coadd.sn_weights(utils.array_to_explist(flux_stack), utils.array_to_explist(ivar_stack), utils.array_to_explist(mask_stack),
                                       sn_smooth_npix=sn_smooth_npix, relative_weights=relative_weights)

    # Because we pass back a weights array, we need to interpolate to assign each detector pixel a weight
    all_wghts = np.ones(all_idx.size)
    for ff in range(numfiles):
        ww = (all_idx == ff)
        all_wghts[ww] = interp1d(wave_spec, weights[ff], kind='cubic',
                                 bounds_error=False, fill_value="extrapolate")(all_wave[ww])
    msgs.info("Optimal weighting complete")
    return all_wghts


def generate_image_subpixel(image_wcs, all_ra, all_dec, all_wave, all_sci, all_ivar, all_wghts,
                            all_spatpos, all_specpos, all_spatid, tilts, slits, astrom_trans, all_dar, bins,
                            all_idx=None, spec_subpixel=10, spat_subpixel=10, combine=False):
    """
    Generate a white light image from the input pixels

    Args:
        image_wcs (`astropy.wcs.WCS`_):
            World coordinate system to use for the white light images.
        all_ra (`numpy.ndarray`_):
            1D flattened array containing the right ascension of each pixel
            (units = degrees)
        all_dec (`numpy.ndarray`_):
            1D flattened array containing the declination of each pixel (units =
            degrees)
        all_wave (`numpy.ndarray`_):
            1D flattened array containing the wavelength of each pixel (units =
            Angstroms)
        all_sci (`numpy.ndarray`_):
            1D flattened array containing the counts of each pixel from all
            spec2d files
        all_ivar (`numpy.ndarray`_):
            1D flattened array containing the inverse variance of each pixel
            from all spec2d files
        all_wghts (`numpy.ndarray`_):
            1D flattened array containing the weights of each pixel to be used
            in the combination
        all_spatpos (`numpy.ndarray`_):
            1D flattened array containing the detector pixel location in the
            spatial direction
        all_specpos (`numpy.ndarray`_):
            1D flattened array containing the detector pixel location in the
            spectral direction
        all_spatid (`numpy.ndarray`_):
            1D flattened array containing the spatid of each pixel
        tilts (`numpy.ndarray`_, list):
            2D wavelength tilts frame, or a list of tilt frames (see all_idx)
        slits (:class:`~pypeit.slittrace.SlitTraceSet`, list):
            Information stored about the slits, or a list of SlitTraceSet (see
            all_idx)
        astrom_trans (:class:`~pypeit.alignframe.AlignmentSplines`, list):
            A Class containing the transformation between detector pixel
            coordinates and WCS pixel coordinates, or a list of Alignment
            Splines (see all_idx)
        all_dar (:class:`~pypeit.coadd3d.DARcorrection`, list):
            A Class containing the DAR correction information, or a list of DARcorrection
            classes. If a list, it must be the same length as astrom_trans.
        bins (tuple):
            A 3-tuple (x,y,z) containing the histogram bin edges in x,y spatial
            and z wavelength coordinates
        all_idx (`numpy.ndarray`_, optional):
            If tilts, slits, and astrom_trans are lists, this should contain a
            1D flattened array, of the same length as all_sci, containing the
            index the tilts, slits, and astrom_trans lists that corresponds to
            each pixel. Note that, in this case all of these lists need to be
            the same length.
        spec_subpixel (:obj:`int`, optional):
            What is the subpixellation factor in the spectral direction. Higher
            values give more reliable results, but note that the time required
            goes as (``spec_subpixel * spat_subpixel``). The default value is 5,
            which divides each detector pixel into 5 subpixels in the spectral
            direction.
        spat_subpixel (:obj:`int`, optional):
            What is the subpixellation factor in the spatial direction. Higher
            values give more reliable results, but note that the time required
            goes as (``spec_subpixel * spat_subpixel``). The default value is 5,
            which divides each detector pixel into 5 subpixels in the spatial
            direction.
        combine (:obj:`bool`, optional):
            If True, all of the input frames will be combined into a single
            output. Otherwise, individual images will be generated.

    Returns:
        `numpy.ndarray`_: The white light images for all frames
    """
    # Perform some checks on the input -- note, more complete checks are performed in subpixellate()
    _all_idx = np.zeros(all_sci.size) if all_idx is None else all_idx
    if combine:
        numfr = 1
    else:
        numfr = np.unique(_all_idx).size
        if len(tilts) != numfr or len(slits) != numfr or len(astrom_trans) != numfr or len(all_dar) != numfr:
            msgs.error("The following arguments must be the same length as the expected number of frames to be combined:"
                       + msgs.newline() + "tilts, slits, astrom_trans, all_dar")
    # Prepare the array of white light images to be stored
    numra = bins[0].size-1
    numdec = bins[1].size-1
    all_wl_imgs = np.zeros((numra, numdec, numfr))

    # Loop through all frames and generate white light images
    for fr in range(numfr):
        msgs.info(f"Creating image {fr+1}/{numfr}")
        if combine:
            # Subpixellate
            img, _, _ = subpixellate(image_wcs, all_ra, all_dec, all_wave,
                                     all_sci, all_ivar, all_wghts, all_spatpos,
                                     all_specpos, all_spatid, tilts, slits, astrom_trans, all_dar, bins,
                                     spec_subpixel=spec_subpixel, spat_subpixel=spat_subpixel, all_idx=_all_idx)
        else:
            ww = np.where(_all_idx == fr)
            # Subpixellate
            img, _, _ = subpixellate(image_wcs, all_ra[ww], all_dec[ww], all_wave[ww],
                                     all_sci[ww], all_ivar[ww], all_wghts[ww], all_spatpos[ww],
                                     all_specpos[ww], all_spatid[ww], tilts[fr], slits[fr], astrom_trans[fr],
                                     all_dar[fr], bins, spec_subpixel=spec_subpixel, spat_subpixel=spat_subpixel)
        all_wl_imgs[:, :, fr] = img[:, :, 0]
    # Return the constructed white light images
    return all_wl_imgs


def generate_cube_subpixel(outfile, output_wcs, all_ra, all_dec, all_wave, all_sci, all_ivar, all_wghts,
                           all_spatpos, all_specpos, all_spatid, tilts, slits, astrom_trans, all_dar, bins,
                           all_idx=None, spec_subpixel=10, spat_subpixel=10, overwrite=False,
                           whitelight_range=None, debug=False):
    """
    Save a datacube using the subpixel algorithm. Refer to the subpixellate()
    docstring for further details about this algorithm

    Args:
        outfile (str):
            Filename to be used to save the datacube
        output_wcs (`astropy.wcs.WCS`_):
            Output world coordinate system.
        all_ra (`numpy.ndarray`_):
            1D flattened array containing the right ascension of each pixel
            (units = degrees)
        all_dec (`numpy.ndarray`_):
            1D flattened array containing the declination of each pixel (units =
            degrees)
        all_wave (`numpy.ndarray`_):
            1D flattened array containing the wavelength of each pixel (units =
            Angstroms)
        all_sci (`numpy.ndarray`_):
            1D flattened array containing the counts of each pixel from all
            spec2d files
        all_ivar (`numpy.ndarray`_):
            1D flattened array containing the inverse variance of each pixel
            from all spec2d files
        all_wghts (`numpy.ndarray`_):
            1D flattened array containing the weights of each pixel to be used
            in the combination
        all_spatpos (`numpy.ndarray`_):
            1D flattened array containing the detector pixel location in the
            spatial direction
        all_specpos (`numpy.ndarray`_):
            1D flattened array containing the detector pixel location in the
            spectral direction
        all_spatid (`numpy.ndarray`_):
            1D flattened array containing the spatid of each pixel
        tilts (`numpy.ndarray`_, list):
            2D wavelength tilts frame, or a list of tilt frames (see all_idx)
        slits (:class:`~pypeit.slittrace.SlitTraceSet`, list):
            Information stored about the slits, or a list of SlitTraceSet (see
            all_idx)
        astrom_trans (:class:`~pypeit.alignframe.AlignmentSplines`, list):
            A Class containing the transformation between detector pixel
            coordinates and WCS pixel coordinates, or a list of Alignment
            Splines (see all_idx)
        all_dar (:class:`~pypeit.coadd3d.DARcorrection`, list):
            A Class containing the DAR correction information, or a list of DARcorrection
            classes. If a list, it must be the same length as astrom_trans.
        bins (tuple):
            A 3-tuple (x,y,z) containing the histogram bin edges in x,y spatial
            and z wavelength coordinates
        all_idx (`numpy.ndarray`_, optional):
            If tilts, slits, and astrom_trans are lists, this should contain a
            1D flattened array, of the same length as all_sci, containing the
            index the tilts, slits, and astrom_trans lists that corresponds to
            each pixel. Note that, in this case all of these lists need to be
            the same length.
        spec_subpixel (int, optional):
            What is the subpixellation factor in the spectral direction. Higher
            values give more reliable results, but note that the time required
            goes as (``spec_subpixel * spat_subpixel``). The default value is 5,
            which divides each detector pixel into 5 subpixels in the spectral
            direction.
        spat_subpixel (int, optional):
            What is the subpixellation factor in the spatial direction. Higher
            values give more reliable results, but note that the time required
            goes as (``spec_subpixel * spat_subpixel``). The default value is 5,
            which divides each detector pixel into 5 subpixels in the spatial
            direction.
        overwrite (bool, optional):
            If True, the output cube will be overwritten.
        whitelight_range (None, list, optional):
            A two element list that specifies the minimum and maximum
            wavelengths (in Angstroms) to use when constructing the white light
            image (format is: [min_wave, max_wave]). If None, the cube will be
            collapsed over the full wavelength range. If a list is provided an
            either element of the list is None, then the minimum/maximum
            wavelength range of that element will be set by the minimum/maximum
            wavelength of all_wave.
        debug (bool, optional):
            If True, a residuals cube will be output. If the datacube generation
            is correct, the distribution of pixels in the residual cube with no
            flux should have mean=0 and std=1.

    Returns:
        :obj:`tuple`: Four `numpy.ndarray`_ objects containing
        (1) the datacube generated from the subpixellated inputs,
        (2) the corresponding error cube (standard deviation),
        (3) the corresponding bad pixel mask cube, and
        (4) a 1D array containing the wavelength at each spectral coordinate of the datacube.
    """
    # Prepare the header, and add the unit of flux to the header
    hdr = output_wcs.to_header()

    # Subpixellate
    subpix = subpixellate(output_wcs, all_ra, all_dec, all_wave, all_sci, all_ivar, all_wghts, all_spatpos, all_specpos,
                          all_spatid, tilts, slits, astrom_trans, all_dar, bins, all_idx=all_idx,
                          spec_subpixel=spec_subpixel, spat_subpixel=spat_subpixel, debug=debug)
    # Extract the variables that we need
    if debug:
        flxcube, varcube, bpmcube, residcube = subpix
        # Save a residuals cube
        outfile_resid = outfile.replace(".fits", "_resid.fits")
        msgs.info("Saving residuals datacube as: {0:s}".format(outfile_resid))
        hdu = fits.PrimaryHDU(residcube.T, header=hdr)
        hdu.writeto(outfile_resid, overwrite=overwrite)
    else:
        flxcube, varcube, bpmcube = subpix

    # Get wavelength of each pixel
    nspec = flxcube.shape[2]
    wcs_scale = (1.0*output_wcs.spectral.wcs.cunit[0]).to(units.Angstrom).value  # Ensures the WCS is in Angstroms
    wave = wcs_scale * output_wcs.spectral.wcs_pix2world(np.arange(nspec), 0)[0]

    # Check if the user requested a white light image
    if whitelight_range is not None:
        # Grab the WCS of the white light image
        whitelight_wcs = output_wcs.celestial
        # Determine the wavelength range of the whitelight image
        if whitelight_range[0] is None:
            whitelight_range[0] = np.min(all_wave)
        if whitelight_range[1] is None:
            whitelight_range[1] = np.max(all_wave)
        msgs.info("White light image covers the wavelength range {0:.2f} A - {1:.2f} A".format(
            whitelight_range[0], whitelight_range[1]))
        # Get the output filename for the white light image
        out_whitelight = get_output_whitelight_filename(outfile)
        whitelight_img = make_whitelight_fromcube(flxcube, wave=wave, wavemin=whitelight_range[0], wavemax=whitelight_range[1])
        msgs.info("Saving white light image as: {0:s}".format(out_whitelight))
        img_hdu = fits.PrimaryHDU(whitelight_img.T, header=whitelight_wcs.to_header())
        img_hdu.writeto(out_whitelight, overwrite=overwrite)
    # TODO :: Avoid transposing these large cubes
    return flxcube.T, np.sqrt(varcube.T), bpmcube.T, wave


def subpixellate(output_wcs, all_ra, all_dec, all_wave, all_sci, all_ivar, all_wghts, all_spatpos, all_specpos,
                 all_spatid, tilts, slits, astrom_trans, all_dar, bins, all_idx=None,
                 spec_subpixel=10, spat_subpixel=10, debug=False):
    r"""
    Subpixellate the input data into a datacube. This algorithm splits each
    detector pixel into multiple subpixels, and then assigns each subpixel to a
    voxel. For example, if ``spec_subpixel = spat_subpixel = 10``, then each
    detector pixel is divided into :math:`10^2=100` subpixels. Alternatively,
    when spec_subpixel = spat_subpixel = 1, this corresponds to the nearest grid
    point (NGP) algorithm.

    Important Note: If spec_subpixel > 1 or spat_subpixel > 1, the errors will
    be correlated, and the covariance is not being tracked, so the errors will
    not be (quite) right. There is a tradeoff one has to make between sampling
    and better looking cubes, versus no sampling and better behaved errors.

    Args:
        output_wcs (`astropy.wcs.WCS`_):
            Output world coordinate system.
        all_ra (`numpy.ndarray`_):
            1D flattened array containing the right ascension of each pixel
            (units = degrees)
        all_dec (`numpy.ndarray`_):
            1D flattened array containing the declination of each pixel (units =
            degrees)
        all_wave (`numpy.ndarray`_):
            1D flattened array containing the wavelength of each pixel (units =
            Angstroms)
        all_sci (`numpy.ndarray`_):
            1D flattened array containing the counts of each pixel from all
            spec2d files
        all_ivar (`numpy.ndarray`_):
            1D flattened array containing the inverse variance of each pixel
            from all spec2d files
        all_wghts (`numpy.ndarray`_):
            1D flattened array containing the weights of each pixel to be used
            in the combination
        all_spatpos (`numpy.ndarray`_):
            1D flattened array containing the detector pixel location in the
            spatial direction
        all_specpos (`numpy.ndarray`_):
            1D flattened array containing the detector pixel location in the
            spectral direction
        all_spatid (`numpy.ndarray`_):
            1D flattened array containing the spatid of each pixel
        tilts (`numpy.ndarray`_, list):
            2D wavelength tilts frame, or a list of tilt frames (see all_idx)
        slits (:class:`~pypeit.slittrace.SlitTraceSet`, list):
            Information stored about the slits, or a list of SlitTraceSet (see
            all_idx)
        astrom_trans (:class:`~pypeit.alignframe.AlignmentSplines`, list):
            A Class containing the transformation between detector pixel
            coordinates and WCS pixel coordinates, or a list of Alignment
            Splines (see all_idx)
        all_dar (:class:`~pypeit.coadd3d.DARcorrection`, list):
            A Class containing the DAR correction information, or a list of DARcorrection
            classes. If a list, it must be the same length as astrom_trans.
        bins (tuple):
            A 3-tuple (x,y,z) containing the histogram bin edges in x,y spatial
            and z wavelength coordinates
        all_idx (`numpy.ndarray`_, optional):
            If tilts, slits, and astrom_trans are lists, this should contain a
            1D flattened array, of the same length as all_sci, containing the
            index the tilts, slits, and astrom_trans lists that corresponds to
            each pixel. Note that, in this case all of these lists need to be
            the same length.
        spec_subpixel (:obj:`int`, optional):
            What is the subpixellation factor in the spectral direction. Higher
            values give more reliable results, but note that the time required
            goes as (``spec_subpixel * spat_subpixel``). The default value is 5,
            which divides each detector pixel into 5 subpixels in the spectral
            direction.
        spat_subpixel (:obj:`int`, optional):
            What is the subpixellation factor in the spatial direction. Higher
            values give more reliable results, but note that the time required
            goes as (``spec_subpixel * spat_subpixel``). The default value is 5,
            which divides each detector pixel into 5 subpixels in the spatial
            direction.
        debug (bool):
            If True, a residuals cube will be output. If the datacube generation
            is correct, the distribution of pixels in the residual cube with no
            flux should have mean=0 and std=1.

    Returns:
        :obj:`tuple`: Three or four `numpy.ndarray`_ objects containing (1) the
        datacube generated from the subpixellated inputs, (2) the corresponding
        variance cube, (3) the corresponding bad pixel mask cube, and (4) the
        residual cube.  The latter is only returned if debug is True.
    """
    # Check for combinations of lists or not
    if all([isinstance(l, list) for l in [tilts, slits, astrom_trans, all_dar]]):
        # Several frames are being combined. Check the lists have the same length
        numframes = len(tilts)
        if len(slits) != numframes or len(astrom_trans) != numframes or len(all_dar) != numframes:
            msgs.error("The following lists must have the same length:" + msgs.newline() +
                       "tilts, slits, astrom_trans, all_dar")
        # Check all_idx has been set
        if all_idx is None:
            if numframes != 1:
                msgs.error("Missing required argument for combining frames: all_idx")
            else:
                all_idx = np.zeros(all_sci.size)
        else:
            tmp = np.unique(all_idx).size
            if tmp != numframes:
                msgs.warn("Indices in argument 'all_idx' does not match the number of frames expected.")
        # Store in the following variables
        _tilts, _slits, _astrom_trans, _all_dar = tilts, slits, astrom_trans, all_dar
    elif all([not isinstance(l, list) for l in [tilts, slits, astrom_trans, all_dar]]):
        # Just a single frame - store as lists for this code
        _tilts, _slits, _astrom_trans, _all_dar = [tilts], [slits], [astrom_trans], [all_dar]
        all_idx = np.zeros(all_sci.size)
        numframes = 1
    else:
        msgs.error("The following input arguments should all be of type 'list', or all not be type 'list':" +
                   msgs.newline() + "tilts, slits, astrom_trans, all_dar")
    # Prepare the output arrays
    outshape = (bins[0].size-1, bins[1].size-1, bins[2].size-1)
    binrng = [[bins[0][0], bins[0][-1]], [bins[1][0], bins[1][-1]], [bins[2][0], bins[2][-1]]]
    flxcube, varcube, normcube = np.zeros(outshape), np.zeros(outshape), np.zeros(outshape)
    if debug:
        residcube = np.zeros(outshape)
    # Divide each pixel into subpixels
    spec_offs = np.arange(0.5/spec_subpixel, 1, 1/spec_subpixel) - 0.5  # -0.5 is to offset from the centre of each pixel.
    spat_offs = np.arange(0.5/spat_subpixel, 1, 1/spat_subpixel) - 0.5  # -0.5 is to offset from the centre of each pixel.
    spat_x, spec_y = np.meshgrid(spat_offs, spec_offs)
    num_subpixels = spec_subpixel * spat_subpixel
    area = 1 / num_subpixels
    all_wght_subpix = all_wghts * area
    all_var = utils.inverse(all_ivar)
    # Loop through all exposures
    for fr in range(numframes):
        # Extract tilts and slits for convenience
        this_tilts = _tilts[fr]
        this_slits = _slits[fr]
        # Loop through all slits
        for sl, spatid in enumerate(this_slits.spat_id):
            if numframes == 1:
                msgs.info(f"Resampling slit {sl+1}/{this_slits.nslits}")
            else:
                msgs.info(f"Resampling slit {sl+1}/{this_slits.nslits} of frame {fr+1}/{numframes}")
            this_sl = np.where((all_spatid == spatid) & (all_idx == fr))
            wpix = (all_specpos[this_sl], all_spatpos[this_sl])
            # Generate a spline between spectral pixel position and wavelength
            yspl = this_tilts[wpix]*(this_slits.nspec - 1)
            tiltpos = np.add.outer(yspl, spec_y).flatten()
            wspl = all_wave[this_sl]
            asrt = np.argsort(yspl)
            wave_spl = interp1d(yspl[asrt], wspl[asrt], kind='linear', bounds_error=False, fill_value='extrapolate')
            # Calculate the wavelength at each subpixel
            this_wave = wave_spl(tiltpos)
            # Calculate the DAR correction at each sub pixel
            ra_corr, dec_corr = _all_dar[fr].correction(this_wave)  # This routine needs the wavelengths to be expressed in Angstroms
            # Calculate spatial and spectral positions of the subpixels
            spat_xx = np.add.outer(wpix[1], spat_x.flatten()).flatten()
            spec_yy = np.add.outer(wpix[0], spec_y.flatten()).flatten()
            # Transform this to spatial location
            spatpos_subpix = _astrom_trans[fr].transform(sl, spat_xx, spec_yy)
            spatpos = _astrom_trans[fr].transform(sl, all_spatpos[this_sl], all_specpos[this_sl])
            # Interpolate the RA/Dec over the subpixel spatial positions
            ssrt = np.argsort(spatpos)
            tmp_ra = all_ra[this_sl]
            tmp_dec = all_dec[this_sl]
            ra_spl = interp1d(spatpos[ssrt], tmp_ra[ssrt], kind='linear', bounds_error=False, fill_value='extrapolate')
            dec_spl = interp1d(spatpos[ssrt], tmp_dec[ssrt], kind='linear', bounds_error=False, fill_value='extrapolate')
            this_ra = ra_spl(spatpos_subpix)
            this_dec = dec_spl(spatpos_subpix)
            # Now apply the DAR correction
            this_ra += ra_corr
            this_dec += dec_corr
            # Convert world coordinates to voxel coordinates, then histogram
            vox_coord = output_wcs.wcs_world2pix(np.vstack((this_ra, this_dec, this_wave * 1.0E-10)).T, 0)
            # Use the "fast histogram" algorithm, that assumes regular bin spacing
            flxcube += histogramdd(vox_coord, bins=outshape, range=binrng, weights=np.repeat(all_sci[this_sl] * all_wght_subpix[this_sl], num_subpixels))
            varcube += histogramdd(vox_coord, bins=outshape, range=binrng, weights=np.repeat(all_var[this_sl] * all_wght_subpix[this_sl]**2, num_subpixels))
            normcube += histogramdd(vox_coord, bins=outshape, range=binrng, weights=np.repeat(all_wght_subpix[this_sl], num_subpixels))
            if debug:
                residcube += histogramdd(vox_coord, bins=outshape, range=binrng, weights=np.repeat(all_sci[this_sl] * np.sqrt(all_ivar[this_sl]), num_subpixels))
    # Normalise the datacube and variance cube
    nc_inverse = utils.inverse(normcube)
    flxcube *= nc_inverse
    varcube *= nc_inverse**2
    bpmcube = (normcube == 0).astype(np.uint8)
    if debug:
        residcube *= nc_inverse
        return flxcube, varcube, bpmcube, residcube
    return flxcube, varcube, bpmcube
