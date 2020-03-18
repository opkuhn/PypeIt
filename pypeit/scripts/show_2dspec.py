#!/usr/bin/env python
#
# See top-level LICENSE file for Copyright information
#
# -*- coding: utf-8 -*-
"""
This script enables the viewing of a processed FITS file
with extras.  Run above the Science/ folder.
"""
import argparse
import os

import numpy as np

from IPython import embed

from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from pypeit import msgs
from pypeit import ginga
from pypeit import slittrace
from pypeit import specobjs

from pypeit.core.parse import get_dnum
from pypeit.images.maskimage import ImageBitMask
from pypeit import masterframe
from pypeit import waveimage
from pypeit import spec2dobj


def parser(options=None):
    parser = argparse.ArgumentParser(description='Display sky subtracted, spec2d image in a '
                                                 'Ginga viewer.  Run above the Science/ folder',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('file', type = str, default = None, help = 'PYPIT spec2d file')
    parser.add_argument('--list', default=False, help='List the extensions only?',
                        action='store_true')
    parser.add_argument('--det', default=1, type=int, help='Detector number')
    parser.add_argument('--showmask', default=False, help='Overplot masked pixels',
                        action='store_true')
    parser.add_argument('--removetrace', default=False, help="Do not overplot traces in the skysub, "
                                                             "sky_resid and resid channels",
                        action = "store_true")
    parser.add_argument('--embed', default=False, help='Upon completion embed in ipython shell',
                        action='store_true')

    return parser.parse_args() if options is None else parser.parse_args(options)


def show_trace(specobjs, det, viewer, ch):

    if specobjs is None:
        return
    in_det = np.where(specobjs.DET == det)[0]
    for kk in in_det:
        trace = specobjs[kk]['TRACE_SPAT']
        obj_id = specobjs[kk].NAME
        ginga.show_trace(viewer, ch, trace, obj_id, color='orange') #hdu.name)


def main(args):

    # List only?
    if args.list:
        hdu = fits.open(args.file)
        hdu.info()
        return

    # Load it up
    spec2DObj = spec2dobj.Spec2DObj.from_file(args.file, args.det)

    # Setup for PYPIT imports
    msgs.reset(verbosity=2)

    # Init
    # TODO: get_dnum needs to be deprecated...
    sdet = get_dnum(args.det, prefix=False)

    # One detector, sky sub for now
#    names = [hdu[i].name for i in range(len(hdu))]
#
#    try:
#        exten = names.index('DET{:s}-PROCESSED'.format(sdet))
#    except:  # Backwards compatability
#        msgs.error('Requested detector {:s} was not processed.\n'
#                   'Maybe you chose the wrong one to view?\n'
#                   'Set with --det= or check file contents with --list'.format(sdet))
#    sciimg = hdu[exten].data
#    try:
#        exten = names.index('DET{:s}-SKY'.format(sdet))
#    except:  # Backwards compatability
#        msgs.error('Requested detector {:s} has no sky model.\n'
#                   'Maybe you chose the wrong one to view?\n'
#                   'Set with --det= or check file contents with --list'.format(sdet))
#    skymodel = hdu[exten].data
#    try:
#        exten = names.index('DET{:s}-MASK'.format(sdet))
#    except ValueError:  # Backwards compatability
#        msgs.error('Requested detector {:s} has no bit mask.\n'
#                   'Maybe you chose the wrong one to view?\n'
#                   'Set with --det= or check file contents with --list'.format(sdet))
#    mask = hdu[exten].data
#    try:
#        exten = names.index('DET{:s}-IVARMODEL'.format(sdet))
#    except ValueError:  # Backwards compatability
#        msgs.error('Requested detector {:s} has no IVARMODEL.\n'
#                   'Maybe you chose the wrong one to view?\n' +
#                   'Set with --det= or check file contents with --list'.format(sdet))
#    ivarmodel = hdu[exten].data
##    # Read in the object model for residual map
#    try:
#        exten = names.index('DET{:s}-OBJ'.format(sdet))
#    except ValueError:  # Backwards compatability
#        msgs.error('Requested detector {:s} has no object model.\n'
#                   'Maybe you chose the wrong one to view?\n' +
#                   'Set with --det= or check file contents with --list'.format(sdet))
#    objmodel = hdu[exten].data

    # Get waveimg
    mdir = spec2DObj.head0['PYPMFDIR']
    if not os.path.exists(mdir):
        mdir_base = os.path.join(os.getcwd(), os.path.basename(mdir))
        msgs.warn('Master file dir: {0} does not exist. Using {1}'.format(mdir, mdir_base))
        mdir=mdir_base

    # Slits
    slits_key = '{0}_{1:02d}'.format(spec2DObj.head0['TRACMKEY'], args.det)
    slit_file = os.path.join(mdir, masterframe.construct_file_name(slittrace.SlitTraceSet, slits_key))
    slits = slittrace.SlitTraceSet.from_file(slit_file)

    wave_key = '{0}_{1:02d}'.format(spec2DObj.head0['ARCMKEY'], args.det)
    waveimg_file = os.path.join(mdir, masterframe.construct_file_name(waveimage.WaveImage, wave_key))

    # Grab the Object

    # Show the bitmask?
    if args.showmask:
        mask_in = spec2DObj.mask
        # Unpack the bitmask
        bitMask = ImageBitMask()
        #bpm, crmask, satmask, minmask, offslitmask, nanmask, ivar0mask, ivarnanmask, extractmask \
        #    = bitMask.unpack(mask)
    else:
        mask_in = None
        bitMask = None

    # Object traces from spec1d file
    spec1d_file = args.file.replace('spec2d', 'spec1d')
    if os.path.isfile(spec1d_file):
        sobjs = specobjs.SpecObjs.from_fitsfile(spec1d_file)
    else:
        sobjs = None
        msgs.warn('Could not find spec1d file: {:s}'.format(spec1d_file) + msgs.newline() +
                  '                          No objects were extracted.')

    ginga.connect_to_ginga(raise_err=True, allow_new=True)

    # Now show each image to a separate channel

    # SCIIMG
    image = spec2DObj.sciimg # Raw science image
    (mean, med, sigma) = sigma_clipped_stats(image[spec2DObj.mask == 0], sigma_lower=5.0, sigma_upper=5.0)
    cut_min = mean - 1.0 * sigma
    cut_max = mean + 4.0 * sigma
    chname_skysub='sciimg-det{:s}'.format(sdet)
    # Clear all channels at the beginning
    viewer, ch = ginga.show_image(image, chname=chname_skysub, waveimg=waveimg_file, clear=True)
    if sobjs is not None:
        show_trace(sobjs, args.det, viewer, ch)
    ginga.show_slits(viewer, ch, slits.left, slits.right, slits.id) #, args.det)

    # SKYSUB
    image = (spec2DObj.sciimg - spec2DObj.skymodel) * (spec2DObj.mask == 0)  # sky subtracted image
    (mean, med, sigma) = sigma_clipped_stats(image[spec2DObj.mask == 0], sigma_lower=5.0, sigma_upper=5.0)
    cut_min = mean - 1.0 * sigma
    cut_max = mean + 4.0 * sigma
    chname_skysub='skysub-det{:s}'.format(sdet)
    # Clear all channels at the beginning
    # TODO: JFH For some reason Ginga crashes when I try to put cuts in here.
    viewer, ch = ginga.show_image(image, chname=chname_skysub, waveimg=waveimg_file,
                                  bitmask=bitMask, mask=mask_in) #, cuts=(cut_min, cut_max),wcs_match=True)
    if not args.removetrace and sobjs is not None:
            show_trace(sobjs, args.det, viewer, ch)
    ginga.show_slits(viewer, ch, slits.left, slits.right, slits.id)


    # SKRESIDS
    chname_skyresids = 'sky_resid-det{:s}'.format(sdet)
    image = (spec2DObj.sciimg - spec2DObj.skymodel) * np.sqrt(spec2DObj.ivarmodel) * (spec2DObj.mask == 0)  # sky residual map
    viewer, ch = ginga.show_image(image, chname_skyresids, waveimg=waveimg_file,
                                  cuts=(-5.0, 5.0), bitmask=bitMask, mask=mask_in)
    if not args.removetrace and sobjs is not None:
            show_trace(sobjs, args.det, viewer, ch)
    ginga.show_slits(viewer, ch, slits.left, slits.right, slits.id)

    # RESIDS
    chname_resids = 'resid-det{:s}'.format(sdet)
    # full model residual map
    image = (spec2DObj.sciimg - spec2DObj.skymodel - spec2DObj.objmodel) * np.sqrt(spec2DObj.ivarmodel) * (spec2DObj.mask == 0)
    viewer, ch = ginga.show_image(image, chname=chname_resids, waveimg=waveimg_file,
                                  cuts = (-5.0, 5.0), bitmask=bitMask, mask=mask_in)
    if not args.removetrace and sobjs is not None:
            show_trace(sobjs, args.det, viewer, ch)
    ginga.show_slits(viewer, ch, slits.left, slits.right, slits.id)


    # After displaying all the images sync up the images with WCS_MATCH
    shell = viewer.shell()
    out = shell.start_global_plugin('WCSMatch')
    out = shell.call_global_plugin_method('WCSMatch', 'set_reference_channel', [chname_resids], {})

    if args.embed:
        embed()

        # Playing with some mask stuff
        #out = shell.start_operation('TVMask')
        #maskfile = '/Users/joe/python/PypeIt-development-suite/REDUX_OUT/Shane_Kast_blue/600_4310_d55/shane_kast_blue_setup_A/crmask.fits'
        #out = shell.call_local_plugin_method(chname_resids, 'TVMask', 'load_file', [maskfile], {})


