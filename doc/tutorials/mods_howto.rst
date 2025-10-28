.. include:: ../include/links.rst

.. _mods_howto:

=================
LBT/MODS HOWTO
=================

Overview
========

This document provides a guide to reducing MODS longslit data with PypeIt: 
(1) from setting up the pypeit file; (2) through the main pypeit run which performs
the wavelength calibration, sky subtraction and extraction; (3) to the final steps of 
flux calibration, coaddition of the extracted 1D spectra and correction of telluric 
absorption. 

There are two sets of MODS spectrograph classes: (1) the original ones (mods1b, mods1r, mods2b
and mods2r) which work on the raw 2D spectra and (2) four new ones (mods1b_proc, mods1r_proc, mods2b_proc 
and mods2r_proc) which were added in v1.18 and work on spectra which have been pre-processed by the `modsCCDRed 
scripts <https://github.com/rwpogge/modsCCDRed>`_. The modsCCDRed scripts overcan-subtract, trim, flat-field and 
flip the red-channel data about the Y axis. Pre-processed data will have the suffix _otf. 
Using modsCCDRed to pre-process the spectra, and then feeding the results into a spectroscopic data reduction pipeline, 
has been the standard procedure for reducing MODS data. 

This tutorial will use the _proc classes, but within the RAW_DATA folder of the shared google drive, linked from :ref:`this page <dev-suite>`, you 
can find sets of both raw and pre-processed data, and pypeit_files for each dataset are in 
`Pypeit-development-suite github <https://github.com/pypeit/PypeIt-development-suite/tree/main/pypeit_files>`_. 

Setup
=====

Organize the data
-----------------

The directory of pre-processed images should contain, for each channel (e.g. MODS1/2 Red/Blue), the science
and spectrophotometric standard star spectra, the set of 3 arcs and the set of slit flats taken through the science slit 
and the standard star slit. MODS longslit arcs always use the 0.6" wide segmented long slit; MODS 
spectrophotometric standards always use the 5"x60" slit; and science data may be taken through any of the 
segmented slits (0.3", 0.6", 0.8", 1", 1.2", or 2.4") or the wide 5" slit, so your directory
could potentially contain spectra taken through 3 different slits. However, all of these data can be combined 
into a single pypeit file, and since the edges of the central segment of the science slit match those of the 5" 
wide slit, only the science slit flats will be used to trace the spectra. This has the advantage that the main 
pypeit run only needs to be done only once per channel and that the standard star trace is available for use 
as a crutch for tracing a science target with a weak continuum.

The MODS1 Dual Grating data on the z~3 quasar, HS1946+7658 will be used for this tutorial.
In this example, the pre-processed blue and red channel data are stored in the folders:

`/PypeIt-development-suite/RAW_DATA/lbt_mods1b_proc/dual_grating_longslit_qso`
and
`/PypeIt-development-suite/RAW_DATA/lbt_mods1r_proc/dual_grating_longslit_qso`

The files within the lbt_mods1b_proc and lbt_mods1r_proc folders are. 

.. code-block:: console

 FILENAME                      OBJECT INSTRUME DICHNAME MASKNAME GRATNAME EXPTIME AIRMASS
 mods1b.20230909.0024_otf.fits Feige_110_dual_grating MODS1B Dual LS60x5 G400L 90.0 1.35
 mods1b.20230909.0028_otf.fits HS1946+7658 MODS1B Dual LS5x60x0.8 G400L 400.0 1.96
 mods1b.20230910.0028_otf.fits Ne_+_Hg_Lamps MODS1B Dual LS5x60x0.6 G400L 2.0 1.00
 mods1b.20230910.0029_otf.fits Kr_+_Xe_Lamps MODS1B Dual LS5x60x0.6 G400L 15.0 1.00
 mods1b.20230910.0030_otf.fits Ar_Lamp MODS1B Dual LS5x60x0.6 G400L 30.0 1.00
 mods1b.20230910.0048_otf.fits QTH1_ND1.5_Dual_LS5x60x0.8_Slit_Flat MODS1B Dual LS5x60x0.8 G400L 20.0 1.00
 mods1b.20230910.0049_otf.fits QTH1_ND1.5_Dual_LS5x60x0.8_Slit_Flat MODS1B Dual LS5x60x0.8 G400L 20.0 1.00
 mods1b.20230910.0051_otf.fits QTH1_UG5_Dual_LS5x60x0.8_Slit_Flat MODS1B Dual LS5x60x0.8 G400L 8.0 1.00
 mods1b.20230910.0052_otf.fits QTH1_UG5_Dual_LS5x60x0.8_Slit_Flat MODS1B Dual LS5x60x0.8 G400L 8.0 1.00
 mods1b.20230910.0080_otf.fits QTH1_ND1.5_Dual_LS60x5_Slit_Flat MODS1B Dual LS60x5 G400L 3.5 1.00
 mods1b.20230910.0083_otf.fits QTH1_UG5_Dual_LS60x5_Slit_Flat MODS1B Dual LS60x5 G400L 1.5 1.00
 
and

.. code-block:: console

 FILENAME                      OBJECT INSTRUME DICHNAME MASKNAME GRATNAME EXPTIME AIRMASS
 mods1r.20230909.0044_otf.fits Feige_110_dual_grating MODS1R Dual LS60x5 G670L 90.0 1.35
 mods1r.20230909.0051_otf.fits HS1946+7658 MODS1R Dual LS5x60x0.8 G670L 400.0 1.96
 mods1r.20230910.0028_otf.fits Ne_+_Hg_Lamps MODS1R Dual LS5x60x0.6 G670L 1.1 1.00
 mods1r.20230910.0029_otf.fits Kr_+_Xe_Lamps MODS1R Dual LS5x60x0.6 G670L 1.0 1.00
 mods1r.20230910.0030_otf.fits Ar_Lamp MODS1R Dual LS5x60x0.6 G670L 1.0 1.00
 mods1r.20230910.0044_otf.fits QTH1_ND1.5_Dual_LS5x60x0.8_Slit_Flat MODS1R Dual LS5x60x0.8 G670L 2.0 1.00
 mods1r.20230910.0047_otf.fits VFLAT10.0_Clear_Dual_LS5x60x0.8_Slit_Flat MODS1R Dual LS5x60x0.8 G670L 2.0 1.00
 mods1r.20230910.0080_otf.fits VFLAT5.0_Clear_Dual_LS60x5_Slit_Flat MODS1R Dual LS60x5 G670L 3.0 1.00
 mods1r.20230910.0081_otf.fits VFLAT5.0_Clear_Dual_LS60x5_Slit_Flat MODS1R Dual LS60x5 G670L 3.0 1.00
 
The input files should be collected into a directory on your machine, e.g. $DATA/dual_grating_longslit_qso/Proc/ (Proc, to 
indicate that these are pre-processed and not raw data). You should create a separate directory, e.g. $DATA/dual_grating_longslit_qso/pypeit_rdx/ 
in which to run pypeit.

Run pypeit_setup
----------------

In the pypeit_rdx/ sub directory, run :ref:`pypeit_setup` to create the pypeit input files for the blue
and red channels. 

.. code-block:: bash

 (pypeit18)% cd pypeit_rdx
 (pypeit18)% pypeit_setup -r $DATA/dual_grating_longslit_qso/Proc/mods1b -s lbt_mods1b_proc -c A
 (pypeit18)% pypeit_setup -r $DATA/dual_grating_longslit_qso/Proc/mods1r -s lbt_mods1r_proc -c A

This will create subdirectories lbt_mods1b_proc_A/ and lbt_mods1r_proc_A/ and write the pypeit files
for the blue and red channels into their respective subdirectories.

.. note::

    If there is more than one configuration in the Proc directory, e.g. MODS1B spectra taken in blue-only and 
    also in dual (dichroic) modes, then -c A will choose only the first configuration.  You should store blue-only, 
    red-only and dual mode data in separate raw(processed) data directories.


Modify the pypeit setup files
-----------------------------

Now, make the following edits to the pypeit setup files:

1) Remove frametypes pixelflat and illumflat from the slit flats since, in this case, they will be used only to trace the slit edges. The 20 spaces keep the columns aligned, but this is only for readability as the columns in pypeit files are separated by '|' and don't need to line up. 

.. code-block:: bash

    sed -i -e s/pixelflat,illumflat,/'\ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ '/ lbt_mods1b_proc_A.pypeit

    sed -i -e s/pixelflat,illumflat,/'\ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ \ '/ lbt_mods1r_proc_A.pypeit

2) Comment out the 5" slit flats. The central slit segment of the science slit will be used to trace the
5" slit edges.

3) Add the slitedges and findobj -> find_min_max parameters indicated below for the blue and red channels. In cases
such as dual grating mode, where the object trace does not illuminate the full detector, it is necessary to set limits 
on the spectral range to be collapsed for object detection. In this example, find_min_max is set to collapse just the 
central part of the spectrum.

Optionally, you can add slitspatnum with the central row, 1544, to indicate that only the central slit segment should
be reduced.


.. note:: 

   The proc classes work in ADUs and not electrons (the conversion gain, which is ~2 e-/ADU, is not applied), 
   so snr_thresh = 10/sqrt(2) will find sources above 10-sigma.

.. code-block:: console

 # User-defined execution parameters
 [rdx]
        spectrograph = lbt_mods1b_proc
        slitspatnum = "DET01:1544"   # reduce only the central slit segment. 
 [calibrations]
        [[slitedges]]
             edge_thresh = 30 
             minimum_slit_length = 50 # slit segments are 60" long
             fit_niter = 3
             sobel_enhance = 5 #(for the blue channel only)
 [reduce]
        [[findobj]]
           find_min_max = 3904,4288 #collapse only the middle of the spectrum when finding objects
           find_numiterfit = 50 #default 9, to improve profile fit and eliminate masking when not desired.
           snr_thresh = 10
           maxnumber_sci = 1
           maxnumber_std = 1

.. note::

   Use of find_numiterfit is discussed under Object Tracing in :ref:`object_finding`. In this case, the slit edges at the blue end 
   of the blue channel may not be that well defined, since neither continuum lamp, quartz-tungsten halogen or variable-intensity 
   incandescent, emits strongly in the blue, and therefore object tracing benefits from more iterations.

Non-standard binning
--------------------

The pipeline has not been run for binned spectra that have been pre-processed by modsCCDRed.
Please contact the developers if there are issues.


Main Run
========

Once the :doc:`../pypeit_file` is ready, the main call is simply:

.. code-block:: bash

    run_pypeit lbt_mods1b_proc_A.pypeit 

The available options may be listed with ``-h``. ``-c`` indicates that only calibrations should be done. 
If this is the first run on a particular dataset, then it is beneficial to run with ``-c`` first, and then 
check that the slit edges have been found correctly and the wavelength solution is good, before proceeding 
with the full reduction.  The ``-o`` indicates that any existing output files should be overwritten. 

The :doc:`../running` doc describes the process in more detail.

For this dataset, a full run takes just under 15 minutes per channel on a macBook Pro with 16Gb RAM.


Inspecting Files
================

Calibrations
------------

The first set are :doc:`../calibrations/calibrations`.

Slit Edges
++++++++++

PypeIt will map the slit edges using the trace frames.

Use the :ref:`pypeit_chk_edges` script (:doc:`../calibrations/edges`) to confirm that the edges of the slits were correctly traced and that all of the slits were found and that there are no extra slits such as might be produced by pinholes or artifacts of the earlier stages of reduction.

For the segmented science slit, all 5 segments are identified as slits, although only those indicated by slitspatnum will be analyzed.

As an example, for MODS1B, 

.. code-block:: bash

   pypeit_chk_edges Calibrations/Edges_A_0_DET01.fits.gz

will generate an image like the one shown below, where the left and right panels display the slit flat and Sobel 
filtered image used to detect edges:

.. figure:: ../figures/mods/mods1_dualblue_edges.png
   :width: 40%


Wavelengths
+++++++++++

To check the quality of the wavelength calibration, open the QA/MF_A.html file and view the Arc_1D_Fit_A_DETO1.html to insure that the lines have been identified and the RMS is low, ideally < 0.1 pixel, or run :ref:`pypeit_chk_wavecalib`.

.. code-block:: console

 pypeit_chk_wavecalib Calibrations/WaveCalib_A_0_DET01.fits                                         
 [INFO]    :: Loading WaveCalib from /Users/olga/Library/CloudStorage/Dropbox/Data/MODS_Sample_Datasets/pypeit_rdx/MODS_LS_rdx/lbt_mods1b_proc_A/Calibrations/WaveCalib_A_0_DET01.fits
 
  N. SpatOrderID minWave Wave_cen maxWave dWave Nlin     IDs_Wave_range    IDs_Wave_cov(%) measured_fwhm  RMS 
 --- ----------- ------- -------- ------- ----- ---- --------------------- --------------- ------------- -----
   0         571     0.0      0.0     0.0 0.000    0     0.000 -     0.000             0.0           0.0 0.000
   1        1097     0.0      0.0     0.0 0.000    0     0.000 -     0.000             0.0           0.0 0.000
   2        1623  2323.1   4407.5  6506.1 0.516   84  3555.321 -  6404.018            68.1           4.6 0.351
   3        2149     0.0      0.0     0.0 0.000    0     0.000 -     0.000             0.0           0.0 0.000
   4        2676     0.0      0.0     0.0 0.000    0     0.000 -     0.000             0.0           0.0 0.000

The wavelength fit used the combination of the 3 arc images, and was based on the identification of 84 lines. 
A relatively high RMS ~ 0.35 is not uncommon for MODS Blue. The RMS is improved if just one or two arcs are used, e.g. if only the Argon lamp spectrum is used, the 35 lines still cover the same wavelength range but the fit has an RMS ~ 0.08 pix. The final results do not appear different and using all 3 spectra is still recommended.

You may refine the wavelength calibration using :ref:`pypeit_identify`. The command below uses the solution found in the pypeit run as a starting point (``-s``) and uses only slit number 2.

.. code-block:: bash

 pypeit_identify Calibrations/Arc_A_0_DET01.fits Calibrations/Slits_A_0_DET01.fits.gz -s --slits 2

This plots the arc spectrum and labels all identified lines. Grey lines indicate those lines detected but not 
identified. How to mark new lines, delete lines and increase or decrease the fit order are all described in
:ref:`pypeit_identify`.

.. figure:: ../figures/mods/mods1b_identify.png

Remember, the default calibration is in vacuum wavelengths. The line lists provided on the `LBTO Sciops MODS webpages <https://scienceops.lbto.org/mods/>`__ have been converted to vacuum wavelengths for use by pypeit. 

Spectra
-------

The code will generate 2D and 1D spectra outputs.  One per science frame, located in the ``Science/`` folder.

2D spectra
++++++++++

One can inspect the two dimensional spectra 
with :ref:`pypeit_show_2dspec`.
It is sometimes helpful to include the ``-showmask`` and ``-removetrace`` options to display the mask and enable a better view of the residuals around the object. The mask values are explained in :ref:`out_masks`.

.. code-block:: bash

   pypeit_show_2dspec Science/spec2d_mods1b.20230909.0028_otf-HS1946+7658_MODS1B_20230909T101314.909.fits

.. figure:: ../figures/mods/mods1b_2dspec_mask_notrace.png
   :width: 40%

The mask is displayed first (upper left), and then from upper left to lower right are the processed raw image, the sky-subtracted image, the sky-subtracted residuals and the residuals with both sky and object profile subtracted. The spec2D files contain more than 4 extensions. These are listed in the header and can be displayed with `ds9 -memf spec2d*fits`.

.. code-block:: console

  EXT0001 = 'DET01-SCIIMG'
  EXT0002 = 'DET01-IVARRAW'
  EXT0003 = 'DET01-SKYMODEL'
  EXT0004 = 'DET01-OBJMODEL'
  EXT0005 = 'DET01-IVARMODEL'
  EXT0006 = 'DET01-TILTS'
  EXT0007 = 'DET01-SCALEIMG'
  EXT0008 = 'DET01-WAVEIMG'
  EXT0009 = 'DET01-BPMMASK'
  EXT0010 = 'DET01-SLITS'
  EXT0011 = 'DET01-WAVESOL'
  EXT0012 = 'DET01-SCI_SPEC_FLEXURE'
  EXT0013 = 'DET01-MED_CHIS'
  EXT0014 = 'DET01-STD_CHIS'
  EXT0015 = 'DET01-DETECTOR'

1D spectra
++++++++++

One can inspect the one dimensional spectra with :ref:`pypeit_show_1dspec`, e.g.

.. code-block:: bash

  pypeit_show_1dspec Science/spec1d_mods1b.20230909.0028_otf-HS1946+7658_MODS1B_20230909T101314.909.fits --ginga

.. figure:: ../figures/mods/mods1r_spec1d.png

or with a custom python script run within your pypeit environment. For example, the code below will plot the boxcar 
and optimally extracted spectra for the first object (spec[0]) in the 1D spec file, spec1dfits.

.. code-block:: python

   #!/usr/bin/env python

   from pypeit import specobjs
   import matplotlib.pyplot as plt

   spec = specobjs.SpecObjs.from_fitsfile(spec1dfits)
   plt.plot(spec[0]['BOX_WAVE'],spec[0]['BOX_COUNTS']
   plt.plot(spec[0]['OPT_WAVE'],spec[0]['OPT_COUNTS']


Spectral Flexure
++++++++++++++++

PypeIt performs spectral flexure correction on science targets, although it does not do this for standard stars. The shift
is determined by cross-correlating a template sky spectrum which has been convolved with a Gaussian with the FWHM of the arc files with the data. 
There is both a global correction, applied to all slits, and local correction, an offset from the global one, for each slit. 
Since most MODS spectra are not taken through the 0.6" slit that is used for the arcs, the procedure may not be exactly correct, but it works well nevertheless.
View the set of spec_flex images in QA/PNGs to verify that the flexure correction looks good. For MODS1R, the global spec_flex_sky PNG looks like this:

.. figure:: ../figures/mods/m1r_flexure.png
   :width: 40%


Flux Calibration 
================

Sensitivity function
------------------------

Pypeit currently uses two algorithms to determine the :ref:`fluxing` -- UVIS for wavelengths < 7000 :math:`\mathrm{\mathring{A}}`, and IR for spectra at longer wavelengths. 
The IR method does not apply extinction but does a detailed fitting of the telluric absorption. MODS users may wish to try both UVIS and IR for the red channel
spectra. 

The script ..ref::`pypeit_sensfunc` generates the sensitivity function from a single spectrophotometric standard star spectrum. Since MODS scripts typically take 3 
back-to-back standard star integrations, it can be reassuring to overplot these (you may need to write a custom script with commands similar to those shown above) to insure that
there was no significant variation between them and to select the best. It reads in a sensfunc input file (e.g. mods1b_dual.sens) and the filename of the 1D spectrum to 
use, e.g.

``pypeit_sensfunc -s mods1b_dual.sens Science/spec1d_mods1b.20230909.0024_otf-Feige110dualgrating_MODS1B_20230909T084730.480.fits --debug -v 2`` 
The debug and high verbosity (v = [0,1,2]) are helpful, especially when starting to reduce a dataset. 

:ref:`pypeit_sensfunc` outputs a fits file which contains the sensitivity function along with 3 PDFs: one of the 
throughput vs wavelength; another of the zeropoint vs wavelength; and a third of the flux-calibrated standard 
star spectrum, with the tabulated spectrum overplotted in green for comparison. 

.. note:: 

   Note that the proc classes do not apply the CCD conversion gain, so the sensitivity functions generated for these 
   will differ from those generated for the original mods classes, but so long as the use is consistent there will be 
   no problem. 

.. note:: 

   Note that pypeit uses spectroscopic zeropoints, which are defined so that a source with a flat spectrum in frequency 
   will and AB magntiude equal to the zeropoint will produce 1 photon/s/angstrom on the detector. To convert these 
   zeropoints (:math:`ZP`) to the zeropoints tabulated on the `LBTO Sciops MODS webpages <https://scienceops.lbto.org/mods>`__ (:math:`ZP_m`):

   :math:`ZP_m` = 0.4 :math:`ZP` + 2 log10(:math:`\lambda`) + 0.964 - log10(g)

   where :math:`\lambda` is the wavelength in :math:`\mathrm{\mathring{A}}` and g, the conversion gain in e-/ADU (g=1 for the *proc* classes).

Sample sensfunc input files for the blue (UVIS) and red (IR and UVIS) channels are given below.

Blue
++++

For the blue channel, `mods1b_dual.sens` is:

.. code:: console

   [sensfunc]
      algorithm = UVIS
      extr = OPT
      polyorder = 25 # default is 7, higher order to fit wiggles in dichroic transmission
      extrap_blu = 0.3
      extrap_red = 0.15
      #hydrogen_mask_wid = 15. #default is 10.
      trim_std_pixs = 1400,1500  #for MODS1B
      use_flat = False
      # for algorithm = UVIS
      [[UVIS]] 
         extinct_correct = True
         extinct_file = lbtoextinct.dat

The dual grating standard star spectrum drops precipitously in the blue, at the atmospheric cutoff, and in the red, due to the dichroic 
transmission. For this reason, trim_std_pixs was set to trim the first 1400 and the last 1500 pixels. These values were determined by locating
the wavelength of the sharp turndown on the 1D spectrum plot and then reading off the corresponding pixel value on the 2D spectrum plot.
The `extrap_blu` and `extrap_red` were set to extrapolate the fit to endpoints of the spectral range.

The `polyorder` has been increased over the default to fit through wiggles in the dichroic transmission function.

Hydrogen absorption lines are masked, and the default 10 :math:`\mathrm{\mathring{A}}` setting appears to be fine.
Uncorrected flexure causes 'P-Cygni'-like profiles for the hydrogen lines, but because these are masked, the fit is 
unaffected. But there is an absorption line, probably HeII/5411 angstroms, in some standards (e.g. Feige 110) which is 
not masked and contributes a small-scale blip in the sensitivity function.

.. figure:: ../figures/mods/mods1b_dual_sensfuncQA.png
   :width: 40%

Red
+++

For the red channel, `mods_1r_dual.sens` is:

.. code:: console

   [sensfunc]
      algorithm = IR
      extr = OPT
      polyorder = 35 # a high order to fit through wiggles in the dichroic transmission
      #hydrogen_mask_wid = 15. #default is 10.
      trim_std_pixs = 1600,1100  #for MODS1R Dual Grating 5635 - 10272 angstroms
      extrap_blu = 0.4
      extrap_red = 0.1
      use_flat = False

The zeropoints vs wavelength PDF is shown below.

.. figure:: ../figures/mods/mods1r_dual_sensfuncQA.png
   :width: 40%

And to illustrate that the fits to the sensitivity function account for the wiggles in the dichroic transmission curves,
the MODS1 Dual Red and Dual Blue zeropoints output by pypeit_sensfunc are plotted together with the MODS1 dichroic 
transmission curves, below.

.. figure:: ../figures/mods/mods1_dichroic_wiggles.png
   :width: 40%


Flux Calibrating the spectra
----------------------------

Setup files for the next three steps: flux calibrating the spectra, coadding these, and correcting for
telluric absorption; are generated with a single script, :ref:`pypeit_flux_setup`.

Coadding the 1D spectra
-----------------------

Correcting the Telluric Absorption
----------------------------------
