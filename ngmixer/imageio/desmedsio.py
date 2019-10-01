from __future__ import print_function
import os, sys
import numpy
import copy
import fitsio

from .medsio import MEDSImageIO, verify_meds
from .. import files
from .. import nbrsfofs
from .. import util
from ..util import print_with_verbosity, \
    radec_to_unitvecs_ruv, \
    radec_to_thetaphi, \
    thetaphi_to_unitvecs_ruv, \
    MissingDataError

import meds

from pprint import pprint

# flagging
IMAGE_FLAGS_SET=2**0
PSF_IN_BLACKLIST=2**1
PSF_MISSING_S2N=2**2
PSF_LOW_S2N=2**3
PSF_FILE_READ_ERROR=2**4


DESY4_BADPIX_MAP={
    "BPM":          1,  #/* set in bpm (hot/dead pixel/column)        */
    "SATURATE":     2,  #/* saturated pixel                           */
    "INTERP":       4,  #/* interpolated pixel                        */
    "BADAMP":       8,  #/* Data from non-functional amplifier        */
    "CRAY":        16,  #/* cosmic ray pixel                          */
    "STAR":        32,  #/* bright star pixel                         */
    "TRAIL":       64,  #/* bleed trail pixel                         */
    "EDGEBLEED":  128,  #/* edge bleed pixel                          */
    "SSXTALK":    256,  #/* pixel potentially effected by xtalk from  */
                        #/*       a super-saturated source            */
    "EDGE":       512,  #/* pixel flag to exclude CCD glowing edges   */
    "STREAK":    1024,  #/* pixel associated with streak from a       */
                        #/*       satellite, meteor, ufo...           */
    "SUSPECT":   2048,  #/* nominally useful pixel but not perfect    */
    "FIXED":     4096,  #/* corrected by pixcorrect                   */
    "NEAREDGE":  8192,  #/* suspect due to edge proximity             */
    "TAPEBUMP": 16384,  #/* suspect due to known tape bump            */
}

DESY3_BADPIX_MAP={
    "BPM":          1,  #/* set in bpm (hot/dead pixel/column)        */
    "SATURATE":     2,  #/* saturated pixel                           */
    "INTERP":       4,  #/* interpolated pixel                        */
    "BADAMP":       8,  #/* Data from non-functional amplifier        */
    "CRAY":        16,  #/* cosmic ray pixel                          */
    "STAR":        32,  #/* bright star pixel                         */
    "TRAIL":       64,  #/* bleed trail pixel                         */
    "EDGEBLEED":  128,  #/* edge bleed pixel                          */
    "SSXTALK":    256,  #/* pixel potentially effected by xtalk from  */
                        #/*       a super-saturated source            */
    "EDGE":       512,  #/* pixel flag to exclude CCD glowing edges   */
    "STREAK":    1024,  #/* pixel associated with streak from a       */
                        #/*       satellite, meteor, ufo...           */
    "SUSPECT":   2048,  #/* nominally useful pixel but not perfect    */
}
#SUSPECT includes tapebump


# SVMEDS
class SVDESMEDSImageIO(MEDSImageIO):

    def __init__(self, *args, **kw):
        conf = args[0]

        conf['use_psf_rerun'] = conf.get('use_psf_rerun',False)
        conf['center_psf'] = conf.get('center_psf',False)

        if conf['use_psf_rerun']:
            rerun=conf['psf_rerun_version']
            raise RuntimeError("old blacklist not supported")
            self._load_psf_blacklist(rerun)

        if 'psf_s2n_checks' in conf:
            self._load_psf_s2n(conf)


        super(SVDESMEDSImageIO,self).__init__(*args, **kw)

        # call this aftersuper init to over ride flags
        self._set_extra_mask_flags()

        self._load_image_metadata()

    def _set_extra_mask_flags(self):
        flag_list = self.conf.get('extra_mask_flag_list',None)

        if flag_list is not None:

            flags = 0
            for flagname in flag_list:
                flags += DESY3_BADPIX_MAP[flagname]

            self.conf['extra_mask_flags'] = flags

    def _load_image_metadata(self):
        """
        tiling was not saved an any existing DES MEDS files,
        so extract it if needed

        Other missing metadata can also be checked for here
        and loaded.  We should cache this
        """
        get_extra_meta=False

        self._image_metadata={}
        self.conf['tilings'] = self.conf.get('tilings',None)

        if self.conf['tilings'] is not None:
            get_extra_meta=True

        if get_extra_meta:
            print("    getting extra image metadata")
            desdata=files.get_desdata()
            meds_desdata=self.meds_list[0]._meta['DESDATA'][0]

            for band in self.iband:

                bmeta={}
                ii=self.meds_list[band].get_image_info()
                meds_meta=self.meds_list[band].get_meta()
                se_ext=meds_meta['se_hdu'][0]-1
                coadd_ext=meds_meta['coadd_hdu'][0]-1

                band_meta=[]
                for i in xrange(ii.size):
                    path=ii['image_path'][i]
                    path=path.replace(meds_desdata,desdata)
                    print("    %d/%d  %s" % (i+1,ii.size,path))

                    if i==0:
                        ext=coadd_ext
                    else:
                        ext=se_ext
                    h=fitsio.read_header(path, ext=ext)

                    band_meta.append(h)
                self._image_metadata[band] = band_meta

    '''
    def _get_offchip_nbr_psf_obs_and_jac(self,band,cen_ind,cen_mindex,cen_obs,nbr_ind,nbr_mindex,nbrs_obs_list):
        assert False,'        FIXME: off-chip nbr %d for cen %d' % (nbr_ind+1,cen_ind+1)
        return None,None
    '''

    def get_file_meta_data(self):
        meds_meta_list = self.meds_meta_list
        dt = meds_meta_list[0].dtype.descr

        if 'config_file' in self.conf:
            tmp,config_file = os.path.split(self.conf['config_file'])
            clen=len(config_file)
            dt += [('ngmixer_config','S%d' % clen)]

        mydesdata=files.get_desdata()
        flen=max([len(mf.replace(mydesdata,'${DESDATA}')) for mf in self.meds_files_full] )
        dt += [('meds_file','S%d' % flen)]

        dt += [('ngmixer_DESDATA','S%d' % len(mydesdata))]

        nband=len(self.meds_files_full)
        meta=numpy.zeros(nband, dtype=dt)

        for band in xrange(nband):
            meds_file = self.meds_files_full[band]
            meds_meta=meds_meta_list[band]
            mnames=meta.dtype.names
            for name in meds_meta.dtype.names:
                if name in mnames:
                    meta[name][band] = meds_meta[name][0]

            if 'config_file' in self.conf:
                meta['ngmixer_config'][band] = config_file
            meta['meds_file'][band] = meds_file.replace(mydesdata,'${DESDATA}')
            meta['ngmixer_DESDATA'][band] = mydesdata

        return meta

    def _get_image_flags(self, band, mindex):
        """
        find images associated with the object and get the image flags
        Also add in the psf flags, eventually incorporated into meds
        """
        meds=self.meds_list[band]
        ncutout=meds['ncutout'][mindex]

        file_ids = meds['file_id'][mindex, 0:ncutout]
        image_flags = self.all_image_flags[band][file_ids]

        return image_flags

    def _get_meds_orig_filename(self, meds, mindex, icut):
        """
        Get the original filename
        """
        file_id=meds['file_id'][mindex, icut]
        ii=meds.get_image_info()
        return ii['image_path'][file_id]

    def get_meta_data_dtype(self):
        dt = super(SVDESMEDSImageIO, self).get_meta_data_dtype()

        desdata=files.get_desdata()
        rlen = len(self.meds_files_full[0]\
                       .replace(desdata,'${DESDATA}')\
                       .split('/')[3])
        dt += [('coadd_run','S%d' % rlen)]
        return dt

    def _get_multi_band_observations(self, mindex):
        coadd_mb_obs_list, mb_obs_list = super(SVDESMEDSImageIO, self)._get_multi_band_observations(mindex)
        desdata=files.get_desdata()
        run = self.meds_files_full[0]\
            .replace(desdata,'${DESDATA}')\
            .split('/')[3]
        coadd_mb_obs_list.meta['meta_data']['coadd_run'] = run
        mb_obs_list.meta['meta_data']['coadd_run'] = run
        return coadd_mb_obs_list, mb_obs_list

    def _should_use_obs(self, band, mindex, icut):

        use=super(SVDESMEDSImageIO,self)._should_use_obs(band, mindex, icut)
        if use and icut > 0:

            tilings=self.conf.get('tilings',None)

            if tilings is not None:
                meds=self.meds_list[band]
                file_id=meds['file_id'][mindex, icut]
                tiling = self._image_metadata[band][file_id]['tiling']
                if tiling not in tilings:
                    #print("        image tiling:",tiling,
                    #      "not in requested tilings",tilings)
                    use=False

        return use

    def _get_band_observations(self, band, mindex):
        """
        if fitting with a gaussian mixture fitter, and
        image is not surface brightness, convert it

        galsim fitters are never surface brightness fitters
        """
        from ngmix.gexceptions import GMixFatalError

        coadd_obs_list, obs_list = \
            super(SVDESMEDSImageIO, self)._get_band_observations(band, mindex)

        imtype = self.conf['imageio']['image_type']
        fitter_type = self.conf['fitter_type']

        if imtype == 'surface_brightness' and 'galsim' in fitter_type:

            # multiply by jacobian scale^2 to change from surface brightness
            # flux image type
            for olist in [coadd_obs_list, obs_list]:
                for obs in olist:
                    if obs.meta['flags'] == 0:
                        pixel_scale2 = obs.jacobian.scale**2
                        pixel_scale4 = pixel_scale2*pixel_scale2

                        image = obs.image * pixel_scale2
                        weight = obs.weight / pixel_scale4

                        try:
                            obs.set_image(image, update_pixels=False)
                            obs.set_weight(weight)
                        except GMixFatalError as err:
                            print(str(err))
                            # we never should have got here, because this
                            # error was caught in get_band_obserrvation.
                            # but it is happening in real code, so lets deal
                            # with it using a hack
                            weight[0, 0] = 1.0
                            obs.set_weight(weight)

                        if obs.weight_raw is not None:
                            obs.weight_raw /= pixel_scale4
                        if obs.weight_us is not None:
                            obs.weight_us /= pixel_scale4

        elif imtype != 'surface_brightness' and 'galsim' not in fitter_type:

            # divide by jacobian scale^2 to convert to surface brightness
            for olist in [coadd_obs_list, obs_list]:
                for obs in olist:
                    if obs.meta['flags'] == 0:
                        pixel_scale2 = obs.jacobian.scale**2
                        pixel_scale4 = pixel_scale2*pixel_scale2

                        image = obs.image / pixel_scale2
                        weight = obs.weight * pixel_scale4

                        try:
                            obs.set_image(image, update_pixels=False)
                            obs.set_weight(weight)
                        except GMixFatalError as err:
                            print(str(err))
                            # we never should have got here, because this
                            # error was caught in get_band_obserrvation.
                            # but it is happening in real code, so lets deal
                            # with it using a hack
                            weight[0, 0] = 1.0
                            obs.set_weight(weight)

                        if obs.weight_raw is not None:
                            obs.weight_raw *= pixel_scale4
                        if obs.weight_us is not None:
                            obs.weight_us *= pixel_scale4

        return coadd_obs_list, obs_list

    def get_epoch_meta_data_dtype(self):
        dt = super(SVDESMEDSImageIO, self).get_epoch_meta_data_dtype()
        dt += [('image_id','i8')]  # image_id specified in meds creation, e.g. for image table
        return dt

    def _fill_obs_meta_data(self,obs, band, mindex, icut):
        """
        fill meta data to be included in output files
        """
        super(SVDESMEDSImageIO, self)._fill_obs_meta_data(obs, band, mindex, icut)
        meds=self.meds_list[band]
        file_id  = meds['file_id'][mindex,icut].astype('i4')
        image_id = meds._image_info[file_id]['image_id']
        obs.meta['meta_data']['image_id'][0]  = image_id

    def _load_psf_data(self):
        self.psf_lists = self._get_psf_lists()

    def _get_psf_image(self, band, mindex, icut):
        """
        Get an image representing the psf
        """
        pconf=self.conf['imageio']['psfs']
        if pconf['type']=='infile':
            return super(SVDESMEDSImageIO,self)._get_psf_image(
                band, mindex, icut,
            )

        meds=self.meds_list[band]
        file_id=meds['file_id'][mindex,icut]

        psf_obj=self.psf_lists[band][file_id]

        row=meds['orig_row'][mindex,icut]
        col=meds['orig_col'][mindex,icut]

        # currently PIFF always centers
        if self.conf['center_psf'] and pconf['type']=='psfex':
            row,col=round(row),round(col)

        im=psf_obj.get_rec(row,col)
        cen=psf_obj.get_center(row,col)

        im=im.astype('f8', copy=False)

        sigma_pix=psf_obj.get_sigma()

        if 'trim_psf' in self.conf and icut > 0:
            im,cen=self._trim_psf(im, cen)

        return im, cen, sigma_pix, psf_obj['filename']

    def _trim_psf(self, im, cen):
        dims=self.conf['trim_psf']['dims']

        rowstart=int(cen[0]-dims[0]/2.0+0.5)
        rowend=int(cen[0]+dims[0]/2.0+0.5)

        colstart=int(cen[1]-dims[1]/2.0+0.5)
        colend=int(cen[1]+dims[1]/2.0+0.5)

        newim = im[rowstart:rowend, colstart:colend]
        newcen=cen.copy()
        newcen[0]=cen[0]-rowstart
        newcen[1]=cen[1]-rowstart

        '''
        print("Trimming psf to:",dims)
        print("new center:",newcen)
        w=numpy.where(newim == 0.0)
        print("number of zeros:",w[0].size)
        '''

        return newim, newcen

    def _get_blacklist_dir(self):
        """
        location for DES black lists
        """
        dir='$DESDATA/EXTRA/blacklists'
        return os.path.expandvars(dir)

    def _get_psf_blacklist_file(self, rerun):
        """
        location of DES psfex blacklists for reruns outside
        of DESDM
        """
        dir=self._get_blacklist_dir()
        fname='psfex-%s.txt' % rerun
        return os.path.join(dir,fname)

    def _get_psf_blacklist_key(self, run, expname, ccd):
        """
        this is our unique key into the blacklist
        """
        key='%s-%s-%02d' % (run,expname,ccd)
        return key

    '''
    def _load_psf_blacklist(self, rerun):
        """
        each psfex rerun has an associated blacklist file
        in a standard location.  Read this and make
        a dictionary keyed by the image metadata
        """
        fname=self._get_psf_blacklist_file(rerun)
        print("loading psf blacklist from:",fname)

        blacklist={}
        with open(fname) as fobj:
            for line in fobj:
                data=line.strip().split()

                run     = data[0]
                expname = data[1]
                ccd     = int(data[2])
                flags   = int(data[3])

                key=self._get_psf_blacklist_key(run, expname, ccd)

                blacklist[key] = flags

        self._psf_blacklist=blacklist
    '''

    def _load_psf_s2n(self, conf):
        fname=conf['psf_s2n_checks']['file']
        print("loading psf s/n:",fname)
        self._psf_s2n = fitsio.read(fname)

    def _get_psf_lists(self):
        """
        Load psf objects for each of the SE images
        include the coadd so we get  the index right
        """
        print('loading psf')

        desdata=files.get_desdata()

        psf_lists=[]
        for band in self.iband:
            meds=self.meds_list[band]

            psf_list = self._get_psf_objects(meds,band)
            psf_lists.append( psf_list )

        return psf_lists

    def _psf_path_from_image_path(self, meds, image_path):
        """
        infer the psf path from the image path.

        old SV code only works for psfex
        """
        desdata=files.get_desdata()
        meds_desdata=meds._meta['DESDATA'][0]

        psf_path=image_path.replace('.fits.fz','_psfcat.psf')
        if desdata not in psf_path:
            psf_path=psf_path.replace(meds_desdata,desdata)

        if self.conf['use_psf_rerun'] and 'coadd' not in psf_path:
            psfparts=psf_path.split('/')
            psfparts[-6] = 'EXTRA' # replace 'OPS'
            psfparts[-3] = 'psfex-rerun/%s' % self.conf['psf_rerun_version'] # replace 'red'
            psf_path='/'.join(psfparts)

        return psf_path

    def _extract_piff_key(self, psf_path):
        """
        D00243626_i_c30_r2363p01_piff.fits
        """
        bname=os.path.basename(psf_path)
        bs=bname.split('_')
        exp=bs[0]
        ccd=bs[2].replace('c','')

        key='%s-%s' % (exp, ccd)
        return key

    def _read_piff_exp_info(self, expname):
        import desmeds
        pconf=self.conf['imageio']['psfs']
        expnum = int( expname[1:] )
        fname=files.get_piff_exp_summary_file(
            pconf['piff_run'],
            expnum,
        )
        print("reading piff info:",fname)
        if not os.path.exists(fname):
            if pconf['allow_missing']:
                print("    missing exposure:",fname)
                return None
            else:
                raise RuntimeError("Missing piff exposure:",fname)
        else:
            return fitsio.read(fname, ext='info')

    def _get_piff_info(self, expname, ccd):
        if not hasattr(self, '_piff_info'):
            self._piff_info = {}
        all_info = self._piff_info

        if expname not in all_info:
            all_info[expname] = self._read_piff_exp_info(expname)

        expinfo = all_info[expname]
        if expinfo is None:
            return None

        ccdnum = int(ccd)
        w,=numpy.where(expinfo['ccdnum'] == ccdnum)
        if w.size == 0:
            raise RuntimeError("piff info for %s %s not found" % (expname,ccd))
        
        return expinfo[w[0]]

    def _replace_piff_dir(self, path):
        """
        replace actual base path with env variable specifier $PIFF_DATA_DIR
        """
        edir = os.environ['PIFF_DATA_DIR']
        paths = path.split('/')[-3:]

        paths = [edir] + paths

        return '/'.join(paths)
 
    def _get_piff_path(self, info):
        path = info['piff_file']
        path = self._replace_piff_dir(path)
        return path


    def _get_piff_object(self, impath):
        """
        read a single PIFF object
        """
        pconf=self.conf['imageio']['psfs']

        flags=0
        psf_obj=None

        expname, ccd, key = self._get_expccd_and_key(impath)
        info = self._get_piff_info(expname, ccd)

        if info is None or info['flag'] != 0 or info['ccdnum']==31:
            flags=PSF_IN_BLACKLIST
        else:
            psf_path = self._get_piff_path(info)

            # we expect a well-formed, existing file if there are no flags set
            if not os.path.exists(psf_path):
                print("missing piff file: %s" % psf_path)
                #flags |= PSF_FILE_READ_ERROR
                raise MissingDataError("missing psf file: %s" % psf_path)
            else:
                print_with_verbosity("loading: %s" % psf_path,verbosity=2)
                psf_obj = PIFFWrapper(psf_path, pconf['stamp_size'])

        return psf_obj, flags


    def _get_psfex_object(self, psf_path):
        """
        read a single PSFEx object
        """
        from psfex import PSFExError, PSFEx
        flags=0
        psf_obj=None
        if self.conf['use_psf_rerun'] and 'coadd' not in psf_path:
            # in Mike's reruns, sometimes the files are corrupted or missing,
            # but these should all be in the blacklist
            fs=psf_path.split('/')
            run=fs[-5]
            expname=fs[-2]
            bname=fs[-1]
            bs=bname.split('_')
            ccd=int(bs[2])

            key=self._get_psfex_blacklist_key(run, expname, ccd)

            if key in self._psf_blacklist:
                print("   psfex in blacklist, flagging:",psf_path)
                flags |= PSF_IN_BLACKLIST

            if flags == 0 and 'psf_s2n_checks' in self.conf:
                pc=self.conf['psf_s2n_checks']
                pkey=self._psf_s2n['key']
                w,=numpy.where(key==pkey)
                if w.size == 0:
                    print("   psfex bad s2n, flagging:",psf_path)
                    flags |= PSF_MISSING_S2N
                else:
                    s2n_key=pc['key']
                    s2n=self._psf_s2n[s2n_key][w]
                    if s2n < pc['s2n_min']:
                        print("   psfex %s %g < %g" % (s2n_key,s2n,pc['s2n_min']))
                        flags |= PSF_LOW_S2N

        if flags == 0:
            # we expect a well-formed, existing file if there are no flags set
            if not os.path.exists(psf_path):
                #print("missing psfex: %s" % psf_path)
                #flags |= PSF_FILE_READ_ERROR
                raise MissingDataError("missing psfex: %s" % psf_path)
            else:
                print_with_verbosity("loading: %s" % psf_path,verbosity=2)
                try:
                    psf_obj=PSFEx(psf_path)
                except (PSFExError,IOError) as err:
                    #print("problem with psfex file "
                    #      "'%s': %s " % (psf_path,str(err)))
                    #flags |= PSF_FILE_READ_ERROR
                    raise MissingDataError("problem with psfex file "
                                           "'%s': %s " % (psf_path,str(err)))
        return psf_obj, flags

    def _get_psf_objects(self, meds, band):
        """
        Load psf objects for all images
        """

        pconf=self.conf['imageio']['psfs']

        psf_list=[]

        info=meds.get_image_info()
        nimage=info.size
        nflagged=0
        for i in xrange(nimage):
            psf_obj=None

            # don't even bother if we are going to skip this image
            flags = self.all_image_flags[band][i]

            # assuming coadd is first
            if (i==0) and not self.conf['fit_coadd_galaxy']:
                print("skipping coadd psf")
                self.all_image_flags[band][i] |= 1
            else:
                if (flags & self.conf['image_flags2check']) == 0:

                    # psf_obj might be None with flags set

                    impath=info['image_path'][i].strip()
                    if pconf['type']=='piff':
                        psf_obj, psf_flags = self._get_piff_object(impath)
                    else:
                        if i==0:
                            psf_path=self._coadd_psf_map[band]
                        else:
                            psf_path = self._psf_path_from_image_path(meds, impath)

                        psf_path=os.path.expandvars(psf_path)
                        psf_obj, psf_flags = self._get_psfex_object(psf_path)

                    if psf_flags != 0:
                        self.all_image_flags[band][i] |= psf_flags
                        nflagged += 1


            psf_list.append(psf_obj)

        print("    flagged %d/%d psf for band %s" % (nflagged,nimage,band))
        return psf_list

    def _get_replacement_flags(self, filenames):
        from .util import CombinedImageFlags

        if not hasattr(self,'_replacement_flags'):
            fname=os.path.expandvars(self.conf['replacement_flags'])
            print("reading replacement flags: %s" % fname)
            self._replacement_flags=CombinedImageFlags(fname)

        default=self.conf['image_flags2check']
        return self._replacement_flags.get_flags_multi(filenames,default=default)

    def _load_meds_files(self):
        """
        Load all listed meds files
        We check the flags indicated by image_flags2check.  the saved
        flags are 0 or IMAGE_FLAGS_SET
        """

        self.meds_list=[]
        self.meds_meta_list=[]
        self.all_image_flags=[]

        for i,funexp in enumerate(self.meds_files):
            f = os.path.expandvars(funexp)
            print('band %d meds: %s' % (i,f))
            medsi=meds.MEDS(f)
            medsi_meta=medsi.get_meta()
            image_info=medsi.get_image_info()

            if i==0:
                nobj_tot=medsi.size
            else:
                nobj=medsi.size
                if nobj != nobj_tot:
                    raise ValueError("mismatch in meds "
                                     "sizes: %d/%d" % (nobj_tot,nobj))
            self.meds_list.append(medsi)
            self.meds_meta_list.append(medsi_meta)
            image_flags=image_info['image_flags'].astype('i8')

            if 'replacement_flags' in self.conf and self.conf['replacement_flags'] is not None and image_flags.size > 1:
                print("    replacing image flags")
                image_flags[1:] = \
                    self._get_replacement_flags(image_info['image_path'][1:])

            # now we reduce the flags to zero or IMAGE_FLAGS_SET
            # copy out and check image flags just for cutouts
            cimage_flags=image_flags[1:].copy()
            w,=numpy.where( (cimage_flags & self.conf['image_flags2check']) != 0)
            print("    flags set for: %d/%d" % (w.size,cimage_flags.size))
            cimage_flags[:] = 0
            if w.size > 0:
                cimage_flags[w] = IMAGE_FLAGS_SET

            # copy back in reduced flags
            image_flags[1:] = cimage_flags
            self.all_image_flags.append(image_flags)

        verify_meds(self.meds_list)
        self.nobj_tot = self.meds_list[0].size

# SV multifit with one-off WCS
class MOFSVDESMEDSImageIO(SVDESMEDSImageIO):
    def __init__(self,*args,**kwargs):
        super(MOFSVDESMEDSImageIO,self).__init__(*args,**kwargs)

        read_wcs = self.conf.get('read_wcs',False)
        if read_wcs:
            self.wcs_transforms = self._get_wcs_transforms()

    def _get_wcs_transforms(self):
        """
        Load the WCS transforms for each meds file
        """
        import json
        from esutil.wcsutil import WCS

        print('loading WCS')
        wcs_transforms = {}
        for band in self.iband:
            mname = self.conf['meds_files_full'][band]
            wcsname = mname.replace('-meds-','-meds-wcs-').replace('.fits.fz','.fits').replace('.fits','.json')
            print('loading: %s' % wcsname)
            try:
                with open(wcsname,'r') as fp:
                    wcs_list = json.load(fp)
            except:
                assert False,"WCS file '%s' cannot be read!" % wcsname

            wcs_transforms[band] = []
            for hdr in wcs_list:
                wcs_transforms[band].append(WCS(hdr))

        return wcs_transforms

class Y1DESMEDSImageIO(SVDESMEDSImageIO):
    def __init__(self,*args,**kwargs):
        super(Y1DESMEDSImageIO,self).__init__(*args,**kwargs)

        if 'mof' in self.conf:
            self._load_wcs_data()

        # This is currently separate from the above where the full wcs is
        # read.  That is partly because the object does not behave the same way,
        # we would need a wrapper for MOF for example
        if 'astrom' in self.conf['imageio']:
            self._load_astrom()

    def _set_defaults(self):
        super(Y1DESMEDSImageIO,self)._set_defaults()
        self.conf['read_me_wcs'] = self.conf.get('read_me_wcs',False)

        self._set_propagate_saturated_stars()

        self.conf['flag_y1_stellarhalo_masked'] = self.conf.get('flag_y1_stellarhalo_masked',False)

    def _set_propagate_saturated_stars(self):
        """
        check if we should propagate the SATURATE and INTERP flags
        for bright stars into other bands

        do not propagate saturated/interpolated pixels for bright
        stars into other bands/epochs when these bits are also
        set for the pixel
        """

        pconf = self.conf.get('propagate_star_flags',None)
        if pconf is None:
            self.conf['propagate_star_flags'] = dict(propagate = False)
        else:
            if self.conf['propagate_star_flags']['propagate']:
                flag_list = pconf['ignore_when_set']
                mask = 0
                for flag in flag_list:
                    mask += DESY3_BADPIX_MAP[flag]

                pconf['ignore_when_set_mask'] = mask

    def _load_wcs_data(self):
        # should we read from the original file?
        read_wcs = self.conf.get('read_wcs',False)
        if read_wcs:
            self._load_wcs_from_files()
        else:
            self._load_wcs_from_meds()

    def _load_astrom(self):
        """
        missing entries (probably blacklisted) will get a None
        entry in the astroms dict
        """
        from . import desastrom
        reader = desastrom.AstromReader(self.conf['imageio']['astrom'])

        astroms = {}
        for band in self.iband:
            astroms[band] = {}

            info = self.meds_list[band].get_image_info()
            nimage = info.size

            # get coadd file ID            
            # a total hack, but should work!
            # assumes all objects from the same coadd!
            coadd_file_id = numpy.max(numpy.unique(self.meds_list[band]['file_id'][:,0]))
            assert coadd_file_id >= 0,"Could not get coadd_file_id from MEDS file!"

            for file_id in xrange(nimage):
                if file_id != coadd_file_id:
                    path=info['image_path'][file_id].strip()
                    expname, ccds, key = self._get_expccd_and_key(path)

                    if expname[0] == 'D':
                        expname = expname[1:]

                    expnum=int(expname)
                    ccdnum=int(ccds)

                    astroms[band][file_id] = reader.get_wcs(expnum, ccdnum)
                    

        self.astroms = astroms


    def _load_wcs_from_meds(self):
        from esutil.wcsutil import WCS
        import json

        print('loading WCS from meds')
        wcs_transforms = {}
        for band in self.iband:
            wcs_transforms[band] = {}

            info = self.meds_list[band].get_image_info()
            nimage = info.size

            # get coadd file ID            
            # a total hack, but should work!
            # assumes all objects from the same coadd!
            coadd_file_id = numpy.max(numpy.unique(self.meds_list[band]['file_id'][:,0]))
            assert coadd_file_id >= 0,"Could not get coadd_file_id from MEDS file!"

            wcs_dict = json.loads( info['wcs'][0] )
            wcs_transforms[band][coadd_file_id] = WCS(wcs_dict)

            for i in xrange(nimage):
                if i != coadd_file_id:

                    wcs_dict = json.loads( info['wcs'][i] )
                    wcs_transforms[band][i] = WCS(wcs_dict)

        self.wcs_transforms = wcs_transforms

    def _load_wcs_from_files(self):
        """
        Load the WCS transforms for each meds file
        """
        from esutil.wcsutil import WCS

        print('loading WCS from original files')
        wcs_transforms = {}
        for band in self.iband:
            wcs_transforms[band] = {}

            info = self.meds_list[band].get_image_info()
            nimage = info.size
            meta = self.meds_meta_list[band]

            # get coadd file ID            
            # a total hack, but should work!
            # assumes all objects from the same coadd!
            coadd_file_id = numpy.max(numpy.unique(self.meds_list[band]['file_id'][:,0]))
            assert coadd_file_id >= 0,"Could not get coadd_file_id from MEDS file!"
            
            # in image header for coadd
            coadd_path = info['image_path'][coadd_file_id].strip()
            coadd_path = coadd_path.replace(meta['DESDATA'][0],'${DESDATA}')

            if os.path.exists(os.path.expandvars(coadd_path)):
                h = fitsio.read_header(os.path.expandvars(coadd_path),ext=1)
                wcs_transforms[band][coadd_file_id] = WCS(h)
            else:
                wcs_transforms[band][coadd_file_id] = None
                print("warning: missing coadd WCS from image: %s" % coadd_path)

            # in scamp head files for SE
            if self.conf['read_me_wcs']:
                scamp_dir = os.path.join('/'.join(coadd_path.split('/')[:-2]),'QA/coadd_astrorefine_head')
                for i in xrange(nimage):
                    if i != coadd_file_id:
                        scamp_name = os.path.basename(info['image_path'][i].strip()).replace('.fits.fz','.head')
                        scamp_file = os.path.join(scamp_dir,scamp_name)

                        if os.path.exists(os.path.expandvars(scamp_file)):
                            h = fitsio.read_scamp_head(os.path.expandvars(scamp_file))
                            wcs_transforms[band][i] = WCS(h)
                        else:
                            wcs_transforms[band][i] = None
                            print("warning: missing scamp head: %s" % scamp_file)

        self.wcs_transforms = wcs_transforms

    def _get_offchip_nbr_psf_obs_and_jac(self,band,cen_ind,cen_mindex,cen_obs,nbr_ind,nbr_mindex,nbrs_obs_list):
        """
        how this works...

        Simple Version (below):

            1) use coadd WCS to get offset of nbr from central in u,v
            2) use the Jacobian of the central to turn offset in u,v to row,col
            3) return central PSF and nbr's Jacobian
                return cen_obs.get_psf(),J_nbr

        Complicated Version (to do!):

            1) find a fiducial point on the chip where the galaxy's flux falls (either via its pixels in the
               coadd seg map or some other means)
            2) compute Jacobian and PSF model about this point from the SE WCS and PSF models
            3) use the offset in u,v from the fiducial point to the location of the nbr plus the offset in
               pixels of the fiducial point from the central to center the Jacobian properly on the chip
            4) return the new PSF observation and new Jacobian

        NOTE: We don't fit the PSF observation here. The job of this class is to just to prep observations
        for fitting!
        """
        
        # hack for nbrs with no data!
        # FIXME - need to flag these when being read in maybe?
        if self.meds_list[band]['ncutout'][nbr_mindex] == 0:
            return None,None

        # 1) use coadd WCS to get offset in u,v
        # 1a) first get coadd WCS
        assert self.meds_list[band]['file_id'][cen_mindex,0] == \
          self.meds_list[band]['file_id'][nbr_mindex,0], \
          "central and nbr have different coadd file IDs when getting off-chip WCS! cen file_id = %d, nbr file_id = %d"\
          % (self.meds_list[band]['file_id'][cen_mindex,0],self.meds_list[band]['file_id'][nbr_mindex,0])
        coadd_wcs = self.wcs_transforms[band][self.meds_list[band]['file_id'][cen_mindex,0]]

        # 1b) now get positions
        row_cen = self.meds_list[band]['orig_row'][cen_mindex,0]
        col_cen = self.meds_list[band]['orig_col'][cen_mindex,0]
        ra_cen,dec_cen = coadd_wcs.image2sky(col_cen+1.0,row_cen+1.0) # reversed for esutil WCS objects!

        row_nbr = self.meds_list[band]['orig_row'][nbr_mindex,0]
        col_nbr = self.meds_list[band]['orig_col'][nbr_mindex,0]
        ra_nbr,dec_nbr = coadd_wcs.image2sky(col_nbr+1.0,row_nbr+1.0) # reversed for esutil WCS objects!

        # 1c) now get u,v offset
        # FIXME - discuss projection with Mike and Erin
        # right now using vector to point where rhat of nbr hits the tangent plane of the central
        # differs in length from unity by 1/cos(angle between central and nbr)
        # this is also a *tiny* effect!
        rhat_cen,uhat_cen,vhat_cen = radec_to_unitvecs_ruv(ra_cen,dec_cen)
        rhat_nbr,uhat_nbr,vhat_nbr = radec_to_unitvecs_ruv(ra_nbr,dec_nbr)
        cosang = numpy.dot(rhat_cen,rhat_nbr)
        u_nbr = numpy.dot(rhat_nbr,uhat_cen)/cosang/numpy.pi*180.0*60.0*60.0 # arcsec
        v_nbr = numpy.dot(rhat_nbr,vhat_cen)/cosang/numpy.pi*180.0*60.0*60.0 # arcsec
        uv_nbr = numpy.array([u_nbr,v_nbr])

        # 2) use the Jacobian of the central to turn offset in u,v to row,col
        # Jacobian is used like this
        # (u,v) = J x (row-row0,col-col0)
        # so (row,col) of nbr is
        #   (row,col)_nbr = J^(-1) x (u,v) + (row0,col0)
        J = cen_obs.get_jacobian()
        Jinv = numpy.linalg.inv([[J.dudrow,J.dudcol],[J.dvdrow,J.dvdcol]])
        row0,col0 = J.get_cen()
        rowcol_nbr = numpy.dot(Jinv,uv_nbr) + numpy.array([row0,col0])

        # 2a) now get new Jacobian
        J_nbr = J.copy() # or whatever
        J_nbr.set_cen(row=rowcol_nbr[0],col=rowcol_nbr[1])

        # 3) return it!
        print('        did off-chip nbr %d for cen %d:' % (nbr_ind+1,cen_ind+1))
        print('            band,cen_icut:     ',band,cen_obs.meta['icut'])
        print('            u,v nbr:           ',uv_nbr)
        print('            r,c nbr:           ',rowcol_nbr)
        print('            box_size - r,c nbr:',self.meds_list[band]['box_size'][nbr_mindex]- rowcol_nbr)
        return cen_obs.get_psf(),J_nbr

    def _interpolate_maskbits(self,iobj,m1,icutout1,m2,icutout2):
        """

        we want to propagate SATURATE and INTERP pixels through to all
        bands if they are associated with a star. this way if such areas
        are different between bands, we take the largest

        However, do not propagate these flags if certain other flags are set
        that could have caused the saturation. In other words if the flags
        were set for reasons other than the object being too bright

        Y3 bit masks

#define BADPIX_BPM          1  /* set in bpm (hot/dead pixel/column)        */
#define BADPIX_SATURATE     2  /* saturated pixel                           */
#define BADPIX_INTERP       4  /* interpolated pixel                        */
#define BADPIX_BADAMP       8  /* Data from non-functional amplifier        */
#define BADPIX_LOW (BADPIX_BADAMP) /* too little signal- NOT IN USE        */
#define BADPIX_CRAY        16  /* cosmic ray pixel                          */
#define BADPIX_STAR        32  /* bright star pixel                         */
#define BADPIX_TRAIL       64  /* bleed trail pixel                         */
#define BADPIX_EDGEBLEED  128  /* edge bleed pixel                          */
#define BADPIX_SSXTALK    256  /* pixel potentially effected by xtalk from  */
                               /*       a super-saturated source            */
#define BADPIX_EDGE       512  /* pixel flag to exclude CCD glowing edges   */
#define BADPIX_STREAK    1024  /* pixel associated with streak from a       */
                               /*       satellite, meteor, ufo...           */
#define BADPIX_SUSPECT   2048  /* nominally useful pixel but not perfect    */
#define BADPIX_FIXED     4096  /* corrected by pixcorrect                   */
#define BADPIX_NEAREDGE  8192  /* suspect due to edge proximity             */
#define BADPIX_TAPEBUMP 16384  /* suspect due to known tape bump            */


        these are already nulled in the weight maps that we use for Y3
        BADPIX_BPM
        BADPIX_BADAMP
        BADPIX_EDGEBLEED
        BADPIX_EDGE
        BADPIX_CRAY
        BADPIX_SSXTALK
        BADPIX_STREAK
        BADPIX_TRAIL
        """

        rowcen1 = m1['cutout_row'][iobj,icutout1]
        colcen1 = m1['cutout_col'][iobj,icutout1]
        jacob1 = m1.get_jacobian_matrix(iobj,icutout1)
        
        rowcen2 = m2['cutout_row'][iobj,icutout2]
        colcen2 = m2['cutout_col'][iobj,icutout2]
        jacob2 = m2.get_jacobian_matrix(iobj,icutout2)
        
        im1 = m1.get_cutout(iobj,icutout1,type='bmask')
        im2 = m2.get_cutout(iobj,icutout2,type='bmask')
        im2[:,:] = 0
        


        msk = self.conf['propagate_star_flags']['ignore_when_set_mask']

        is_sat_or_interp = (
            (im1 & DESY3_BADPIX_MAP['SATURATE'] != 0) | (im1 & DESY3_BADPIX_MAP['INTERP'] != 0)
        )
        is_bright_star = (im1 & DESY3_BADPIX_MAP['STAR'] != 0)
        not_other_bits = (im1 & msk == 0)

        q = numpy.where(
             is_sat_or_interp 
             & 
             is_bright_star 
             &
             not_other_bits 
        )

        im1[:,:] = 0
        im1[q] = 1
        
        #assert m1['box_size'][iobj] == m2['box_size'][iobj]
        assert m1['id'][iobj] == m2['id'][iobj]

        util.interpolate_image_diffsize(
            rowcen1, colcen1, jacob1, im1, 
            rowcen2, colcen2, jacob2, im2,
        )
        #im2 = util.interpolate_image(rowcen1, colcen1, jacob1, im1, 
        #                              rowcen2, colcen2, jacob2)[0]

        if im1.max() > 0 and False:
            import images
            print(iobj,icutout1,icutout2)
            images.view_mosaic(
                [im1,im2],
                file='/u/ki/esheldon/public_html/tmp/plots/tmp.png',
            )
            if 'q'==raw_input('hit a key: '):
                stop
        return im2
    
    def _get_extra_bitmasks(self,coadd_mb_obs_list,mb_obs_list):        
        marr = self.meds_list
        mindex = mb_obs_list.meta['meds_index']
        
        bmasks = []
        for bandt,mt in enumerate(marr):
            bmask = numpy.zeros((mt['box_size'][mindex],mt['box_size'][mindex])).astype('i4')
            
            # do the coadd
            if len(coadd_mb_obs_list[bandt]) > 0 and coadd_mb_obs_list[bandt][0].meta['flags'] == 0:
                bmask |= mt.get_cutout(mindex,0,type='bmask')
            
            # do each band
            for band,obs_list in enumerate(mb_obs_list):
                for obs in obs_list:
                    if obs.meta['flags'] == 0:                        
                        bmaski = self._interpolate_maskbits(mindex,
                                                            marr[band],
                                                            obs.meta['icut'],
                                                            mt,
                                                            0)
                        bmask |= bmaski
    
            bmasks.append(bmask)
            
        return bmasks

    def _expand_mask(self,bmask,rounds=1):
        cbmask = bmask.copy()
        
        qx_prev,qy_prev = numpy.where(cbmask != 0)
        
        for r in xrange(rounds):
            qx = []
            qy = []
            for ix,iy in zip(qx_prev,qy_prev):
                for dx in [-1,0,1]:
                    iix = ix + dx
                    if iix >= 0 and iix < bmask.shape[0]:
                        for dy in [-1,0,1]:
                            iiy = iy + dy
                            if iiy >= 0 and iiy < bmask.shape[1]:
                                cbmask[iix,iiy] = 1
                                qx.append(iix)
                                qy.append(iiy)
                                
            qx_prev = numpy.array(qx)
            qy_prev = numpy.array(qy)
                            
        return cbmask

    def _prop_extra_bitmasks(self, bmasks, mb_obs_list):
        mindex = mb_obs_list.meta['meds_index']
            
        # interp to each image
        for band,obs_list in enumerate(mb_obs_list):
            m = self.meds_list[band]
            bmask = bmasks[band]
            
            for obs in obs_list:
                if obs.meta['flags'] == 0:
                    # interp
                    icut = obs.meta['icut']
                    
                    rowcen1 = m['cutout_row'][mindex,0]
                    colcen1 = m['cutout_col'][mindex,0]
                    jacob1 = m.get_jacobian_matrix(mindex,0)
                    
                    rowcen2 = m['cutout_row'][mindex,icut]
                    colcen2 = m['cutout_col'][mindex,icut]
                    jacob2 = m.get_jacobian_matrix(mindex,icut)
                    
                    #bmaski = interpolate_image(rowcen1, colcen1, jacob1, bmask,
                    #                           rowcen2, colcen2, jacob2)[0]
                    bmaski = m.get_cutout(mindex,0,type='bmask')
                    bmaski[:,:] = 0
                    util.interpolate_image_diffsize(
                        rowcen1, colcen1, jacob1, bmask,
                        rowcen2, colcen2, jacob2, bmaski,
                    )
                    
                    bmaski = self._expand_mask(bmaski,rounds=2)
                    
                    # now set weights to zero
                    q = numpy.where((bmaski != 0) & (obs.seg == 0))
                    if len(q[0]) > 0:
                        print('    masked %d pixels due to saturation in any band' % q[0].size)
                        if hasattr(obs,'weight_raw'):
                            obs.weight_raw[q] = 0.0
                            
                        if hasattr(obs,'weight_us'):
                            obs.weight_us[q] = 0.0
                            
                        if hasattr(obs,'weight'):
                            obs.weight[q] = 0.0
                            
                        if hasattr(obs,'weight_orig'):
                            obs.weight_orig[q] = 0.0        

    def _flag_y1_stellarhalo_masked_one(self,mb_obs_list):

        starflag = DESY3_BADPIX_MAP['STAR']

        mindex = mb_obs_list.meta['meds_index']
        seg_number = self.meds_list[0]['number'][mindex]
        
        assert mb_obs_list.meta['id'] == self.meds_list[0]['id'][mindex], \
            "Problem getting meds index! check value of mb_obs_list.meta['meds_index']"
        
        flags = 0
        for band,obs_list in enumerate(mb_obs_list):
            for obs in obs_list:
                if obs.meta['flags'] == 0:

                    icut = obs.meta['icut']
                    bmask = self.meds_list[band].get_cutout(mindex,icut,type='bmask')
                    
                    q = numpy.where((bmask & starflag != 0) & (obs.seg == seg_number))
                    
                    if q[0].size > 0:                        
                        flags = 1
                        return flags
                    
        return flags
    
    def _flag_y1_stellarhalo_masked(self,coadd_mb_obs_list,mb_obs_list):
        flags = 0
        flags |= self._flag_y1_stellarhalo_masked_one(coadd_mb_obs_list)
        if flags == 0:
            flags |= self._flag_y1_stellarhalo_masked_one(mb_obs_list)
            
        return flags

    def _get_multi_band_observations(self, mindex):
        coadd_mb_obs_list, mb_obs_list = super(Y1DESMEDSImageIO, self)._get_multi_band_observations(mindex)
        
        # mask extra pixels in saturated stars
        if self.conf['propagate_star_flags']['propagate']:
            # get total OR'ed bit mask
            bmasks = self._get_extra_bitmasks(coadd_mb_obs_list,mb_obs_list)
            self._prop_extra_bitmasks(bmasks,mb_obs_list)

        # flag things where seg map touches a stellar halo as defined by DESDM
        if self.conf['flag_y1_stellarhalo_masked']:            
            flags = self._flag_y1_stellarhalo_masked(coadd_mb_obs_list,mb_obs_list)
            if flags != 0:
                print('    flagged object due to seg map touching masked stellar halo')
                coadd_mb_obs_list.meta['obj_flags'] |= flags
                mb_obs_list.meta['obj_flags'] |= flags

        return coadd_mb_obs_list, mb_obs_list

class Y3DESMEDSImageIO(Y1DESMEDSImageIO):
    """
    this is using Brian Yanny's exposure pattern list format
    """
    def __init__(self, *args, **kw):

        conf=args[0]
        pconf=conf['imageio']['psfs']

        if pconf['type'] != 'piff' and pconf['type'] != 'infile':
            self._load_psf_map(**kw)

        super(Y3DESMEDSImageIO,self).__init__(*args, **kw)
        self._load_psf_blacklist()

    def _load_psf_blacklist(self):
        """
        load the psf blacklist
        """
        pconf=self.conf['imageio']['psfs']
        blacklist={}
        if 'blacklist' in pconf:
            fname=os.path.expandvars(pconf['blacklist'])
            print("reading psf blacklist:",fname)
            data=fitsio.read(fname)
            for d in data:
                if d['bflags'] != 0:
                    try:
                        key = str(d['key'],'utf-8')
                    except:
                        key = str(d['key'])
                    blacklist[key] = d
        self.psf_blacklist=blacklist


    def get_meta_data_dtype(self):
        """
        there is no coadd_run any more, so skip that part from SV/Y1
        """
        return super(SVDESMEDSImageIO, self).get_meta_data_dtype()

    def _get_multi_band_observations(self, mindex):
        """
        there is no coadd_run any more, so skip that part from SV/Y1
        """
        return super(SVDESMEDSImageIO, self)._get_multi_band_observations(mindex)


    def _load_psf_map(self, **kw):
        """
        we fake the coadd psf
        """
        extra_data=kw.get('extra_data',{})

        map_files=extra_data.get('psf_map',None)
        if map_files is None:
            raise RuntimeError("no psf map found")

        psf_map={}
        coadd_psf_map=[]

        for map_file in map_files:
            print("reading psf map:",map_file)
            with open(map_file) as fobj:
                for line in fobj:

                    ls=line.strip().split()

                    if len(ls) == 2:
                        # standard style
                        key=ls[0]
                        pattern=ls[1]
                    elif len(ls)==3:
                        # DESDM style
                        expnum=int(ls[0])
                        ccdnum=int(ls[1])
                        pattern=ls[2]
                        if expnum==-9999:
                            # coadd psf entry
                            coadd_psf_map.append(pattern)
                            continue
                        else:
                            key = 'D%08d-%02d' % (expnum,ccdnum)

                    else:
                        raise ValueError("badly formatted psf map line: '%s'" % line.strip())

                    psf_map[key] = pattern

        assert len(coadd_psf_map)==len(map_files)
        self._psf_map=psf_map
        self._coadd_psf_map=coadd_psf_map

    def _get_expccd_and_key(self, image_path):
        bname = mks(os.path.basename(image_path))

        fs = bname.split('_')
        if bname[0:3] == 'DES':
            expname = fs[2]
            ccd = fs[4][1:]
        else:
            expname = fs[0]
            ccd = fs[2][1:]

        key='%s-%s' % (expname, ccd)

        return expname, ccd, key


    def _psf_path_from_image_path(self, meds, image_path):
        """
        infer the psf path from the image path.
        """

        expname, ccd, key = self._get_expccd_and_key(image_path)

        psf_path = self._psf_map[key]

        psf_path = os.path.expandvars(psf_path)
        return psf_path

    def get_epoch_meta_data_dtype(self):
        dt = super(SVDESMEDSImageIO, self).get_epoch_meta_data_dtype()
        dt += [('image_id','S49')]  # image_id specified in meds creation, e.g. for image table
        return dt

class PIFFWrapper(dict):
    """
    provide an interface consistent with the PSFEx class
    """
    def __init__(self, psf_path, stamp_size):
        import piff
        self.piff_obj=piff.read(psf_path)
        self['filename'] = psf_path
        self['stamp_size'] = stamp_size

        self.center_cache={}

    def get_rec(self, row, col):
        """
        get the psf reconstruction as a numpy array

        image is normalized
        """
        #import images
        #print("file:",self['filename'])
        #print("drawing at",row,col)
        #gsim = self.piff_obj.draw(x=col, y=row)
        # this is how to center the image in PIFF
        gsim = self.piff_obj.draw(
            x=int(col+0.5),
            y=int(row+0.5),
            stamp_size=self['stamp_size'],
        )
        im = gsim.array

        im *= (1.0/im.sum())

        self._cache_center(row, col, im)

        #images.multiview(im, file='test.png')
        #stop
        return im
    
    def get_center(self, row, col):
        """
        get the center location
        """

        key = self._get_center_cache_key(row, col)

        if key not in self.center_cache:
            # this will force a cache
            im = self.get_rec(row, col)

        return self.center_cache[key]
        
    def get_sigma(self):
        """
        pixels
        """
        return numpy.sqrt(4.0/2.0)

    def _cache_center(self, row, col, im):
        """
        cache the center for the get_center call
        """
        key = self._get_center_cache_key(row, col)

        cen = (numpy.array(im.shape)-1.0)/2.0

        self.center_cache[key] = cen

    def _get_center_cache_key(self, row, col):
        key = '%.16g-%.16g' % (row, col)

def mks(val):
    """
    make sure the value is a string, paying mind to python3 vs 2
    """
    if sys.version_info > (3,0,0):
        if isinstance(val, bytes):
            sval = str(val, 'utf-8')
        else:
            sval = str(val)
    else:
        sval = str(val)
    
    return sval


