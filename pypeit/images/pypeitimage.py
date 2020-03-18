""" Module for the PypeItImage include its Mask

.. include common links, assuming primary doc root is up one directory
.. include:: ../links.rst
"""
import numpy as np
import os
import inspect

from astropy.io import fits

from pypeit import msgs
from pypeit import ginga

from pypeit.images import detector_container, maskimage
from pypeit.core import procimg
from pypeit import datamodel
from pypeit import utils

from IPython import embed



class PypeItImage(datamodel.DataContainer):
    """
    Class to hold a single image from a single detector in PypeIt
    and its related images (e.g. ivar, mask).

    Oriented in its spec,spat format

    The intent is to keep this object as light-weight as possible.

    Args:
        image (`np.ndarray`_ or None):
            See datamodel for description
        ivar (`np.ndarray`_, optional):
        rn2img (`np.ndarray`_, optional):
        bpm (`np.ndarray`_, optional):
        crmask (`np.ndarray`_, optional):
        fullmask (`np.ndarray`_, optional):
        detector (:class:`pypeit.images.data_container.DataContainer`):

    Attributes:
        hdu_prefix (str, optional):
            Appended to the HDU name, if provided.
            Mainly used to enable output of multiple PypeItImage objects
            in more complex DataContainers
        head0 (astropy.io.fits.Header):
        detector (:class:`pypeit.images.detector_container.DetectorContainer`):
        files (list):

    """
    # Set the version of this class
    version = '1.0.0'
    #
    datamodel_v100 = {
        'image': dict(otype=np.ndarray, atype=np.floating, desc='Main data image'),
        'ivar': dict(otype=np.ndarray, atype=np.floating, desc='Main data inverse variance image'),
        'rn2img': dict(otype=np.ndarray, atype=np.floating, desc='Read noise squared image'),
        'bpm': dict(otype=np.ndarray, atype=np.integer, desc='Bad pixel mask'),
        'crmask': dict(otype=np.ndarray, atype=np.bool_, desc='CR mask image'),
        'fullmask': dict(otype=np.ndarray, atype=np.integer, desc='Full image mask'),
        'detector': dict(otype=detector_container.DetectorContainer, desc='Detector DataContainer'),
    }
    datamodel = datamodel_v100.copy()

    # For masking
    bitmask = maskimage.ImageBitMask()

    hdu_prefix = None

    @classmethod
    def from_file(cls, ifile):
        """
        Instantiate from a file on disk (FITS file)

        Overloaded :func:`pypeit.datamodel.DataContainer.from_file` to grab Header

        Args:
            ifile (str):

        Returns:
            :class:`pypeit.images.pypeitimage.PypeItImage`:
                Loaded up PypeItImage with the primary Header attached

        """
        slf = super(PypeItImage, cls).from_file(ifile)

        # Header
        slf.head0 = fits.getheader(ifile)

        # Return
        return slf

    @classmethod
    def from_pypeitimage(cls, pypeitImage):
        """
        Generate an instance
        This enables building the Child from the Parent, e.g. a MasterFrame Image

        This is *not* a deepcopy

        Args:
            pypeitImage (:class:`PypeItImage`):

        Returns:
            pypeitImage (:class:`PypeItImage`):

        """

        _d = {}
        for key in pypeitImage.datamodel.keys():
            _d[key] = pypeitImage[key]
        # Instantiate
        slf = cls(**_d)
        # Internals are lost!
        # Return
        return slf

    def __init__(self, image=None, ivar=None, rn2img=None, bpm=None,  # This should contain all datamodel items
                 crmask=None, fullmask=None, detector=None):

        # Setup the DataContainer
        args, _, _, values = inspect.getargvalues(inspect.currentframe())
        _d = {k: values[k] for k in args[1:]}
        # Init
        super(PypeItImage, self).__init__(d=_d)

    def _init_internals(self):
        self.head0 = None
        self.process_steps = None
        self.files = None


    def _bundle(self):
        """
        Over-write default _bundle() method to write one
        HDU per image.  Any extras are in the HDU header of
        the primary image.

        Returns:
            :obj:`list`: A list of dictionaries, each list element is
            written to its own fits extension. See the description
            above.
        """
        d = []
        # Primary image
        d.append(dict(image=self.image))

        # Rest of the datamodel
        for key in self.keys():
            if key in ['image', 'crmask', 'bpm']:
                continue
            # Skip None
            if self[key] is None:
                continue
            # Array?
            if self.datamodel[key]['otype'] == np.ndarray:
                tmp = {}
                tmp[key] = self[key]
                d.append(tmp)
            elif key == 'detector':
                d.append(dict(detector=self.detector))
            else: # Add to header of the primary image
                d[0][key] = self[key]
        # Return
        return d

    @property
    def shape(self):
        return () if self.image is None else self.image.shape

    def build_crmask(self, par, subtract_img=None):
        """
        Generate the CR mask frame

        Mainly a wrapper to :func:`pypeit.core.procimg.lacosmic`

        Args:
            par (:class:`pypeit.par.pypeitpar.ProcessImagesPar`):
                Parameters that dictate the processing of the images.  See
                :class:`pypeit.par.pypeitpar.ProcessImagesPar` for the
                defaults.
            subtract_img (np.ndarray, optional):
                If provided, subtract this from the image prior to CR detection

        Returns:
            np.ndarray: Copy of self.crmask (boolean)

        """
        var = utils.inverse(self.ivar)
        use_img = self.image if subtract_img is None else self.image - subtract_img
        # Run LA Cosmic to get the cosmic ray mask
        self.crmask = procimg.lacosmic(use_img,
                                       self.detector['saturation'],
                                       self.detector['nonlinear'],
                                       varframe=var,
                                       maxiter=par['lamaxiter'],
                                       grow=par['grow'],
                                       remove_compact_obj=par['rmcompact'],
                                       sigclip=par['sigclip'],
                                       sigfrac=par['sigfrac'],
                                       objlim=par['objlim'])
        # Return
        return self.crmask.copy()

    def build_mask(self, saturation=None, mincounts=None, slitmask=None):
        """
        Return the bit value mask used during extraction.

        The mask keys are defined by :class:`ScienceImageBitMask`.  Any
        pixel with mask == 0 is valid, otherwise the pixel has been
        masked.  To determine why a given pixel has been masked::

            bitmask = ScienceImageBitMask()
            reasons = bm.flagged_bits(mask[i,j])

        To get all the pixel masked for a specific set of reasons::

            indx = bm.flagged(mask, flag=['CR', 'SATURATION'])

        Args:
            saturation (float, optional):
                Saturation limit in counts or ADU (needs to match the input image)
                Defaults to self.detector['saturation']
            slitmask (np.ndarray, optional):
                Slit mask image;  Pixels not in a slit are masked
            mincounts (float, optional):
                Defaults to self.detector['mincounts']

        Returns:
            `np.ndarray`_: Copy of the bit value mask for the science image.
        """
        _mincounts = self.detector['mincounts'] if mincounts is None else mincounts
        _saturation = self.detector['saturation'] if saturation is None else saturation
        # Instatiate the mask
        mask = np.zeros_like(self.image, dtype=self.bitmask.minimum_dtype(asuint=True))

        # Bad pixel mask
        if self.bpm is not None:
            indx = self.bpm.astype(bool)
            mask[indx] = self.bitmask.turn_on(mask[indx], 'BPM')

        # Cosmic rays
        if self.crmask is not None:
            indx = self.crmask.astype(bool)
            mask[indx] = self.bitmask.turn_on(mask[indx], 'CR')

        # Saturated pixels
        indx = self.image >= _saturation
        mask[indx] = self.bitmask.turn_on(mask[indx], 'SATURATION')

        # Minimum counts
        indx = self.image <= _mincounts
        mask[indx] = self.bitmask.turn_on(mask[indx], 'MINCOUNTS')

        # Undefined counts
        indx = np.invert(np.isfinite(self.image))
        mask[indx] = self.bitmask.turn_on(mask[indx], 'IS_NAN')

        if self.ivar is not None:
            # Bad inverse variance values
            indx = np.invert(self.ivar > 0.0)
            mask[indx] = self.bitmask.turn_on(mask[indx], 'IVAR0')

            # Undefined inverse variances
            indx = np.invert(np.isfinite(self.ivar))
            mask[indx] = self.bitmask.turn_on(mask[indx], 'IVAR_NAN')

        if slitmask is not None:
            indx = slitmask == -1
            mask[indx] = self.bitmask.turn_on(mask[indx], 'OFFSLITS')

        self.fullmask = mask
        return self.fullmask.copy()

    def update_mask_slitmask(self, slitmask):
        """
        Update a mask using the slitmask

        Args:
            slitmask (`np.ndarray`_):
                Slitmask with -1 values pixels *not* in a slit

        """
        # Pixels excluded from any slit.
        indx = slitmask == -1
        # Finish
        self.fullmask[indx] = self.bitmask.turn_on(self.fullmask[indx], 'OFFSLITS')

    def update_mask_cr(self, crmask_new):
        """
        Update the mask bits for cosmic rays

        The original are turned off and the new
        ones are turned on.

        Args:
            crmask_new (`np.ndarray`_):
                New CR mask
        """
        self.fullmask = self.bitmask.turn_off(self.fullmask, 'CR')
        indx = crmask_new.astype(bool)
        self.fullmask[indx] = self.bitmask.turn_on(self.fullmask[indx], 'CR')

    def sub(self, other, par):
        """
        Subtract one PypeItImage from another
        Extras (e.g. ivar, masks) are included if they are present

        Args:
            other (:class:`PypeItImage`):
            par (:class:`pypeit.par.pypeitpar.ProcessImagesPar`):
                Parameters that dictate the processing of the images.  See
                :class:`pypeit.par.pypeitpar.ProcessImagesPar` for the defaults
        Returns:
            PypeItImage:
        """
        if not isinstance(other, PypeItImage):
            msgs.error("Misuse of the subtract method")
        # Images
        newimg = self.image - other.image

        # Mask time
        outmask_comb = (self.fullmask == 0) & (other.fullmask == 0)

        # Variance
        if self.ivar is not None:
            new_ivar = utils.inverse(utils.inverse(self.ivar) + utils.inverse(other.ivar))
            new_ivar[np.invert(outmask_comb)] = 0
        else:
            new_ivar = None

        # RN2
        if self.rn2img is not None:
            new_rn2 = self.rn2img + other.rn2img
        else:
            new_rn2 = None

        # Files
        new_files = self.files + other.files

        # Instantiate
        new_sciImg = PypeItImage(image=newimg, ivar=new_ivar, bpm=self.bpm, rn2img=new_rn2,
                                 detector=self.detector)
        new_sciImg.files = new_files
        #TODO: KW properly handle adding the bits
        crmask_diff = new_sciImg.build_crmask(par)
        # crmask_eff assumes evertything masked in the outmask_comb is a CR in the individual images
        new_sciImg.crmask = crmask_diff | np.invert(outmask_comb)
        # Note that the following uses the saturation and mincounts held in
        # self.detector
        new_sciImg.build_mask()

        return new_sciImg

    def show(self):
        """
        Show the image in a ginga viewer.
        """
        if self.image is None:
            # TODO: This should fault.
            msgs.warn("No image to show!")
            return
        ginga.show_image(self.image, chname='image')

    def __repr__(self):
        repr = '<{:s}: '.format(self.__class__.__name__)
        # Image
        rdict = {}
        for attr in self.datamodel.keys():
            if hasattr(self, attr) and getattr(self, attr) is not None:
                rdict[attr] = True
            else:
                rdict[attr] = False
        repr += ' images={}'.format(rdict)
        repr = repr + '>'
        return repr


