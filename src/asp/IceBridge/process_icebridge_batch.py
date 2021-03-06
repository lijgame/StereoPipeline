#!/usr/bin/env python
# __BEGIN_LICENSE__
#  Copyright (c) 2009-2013, United States Government as represented by the
#  Administrator of the National Aeronautics and Space Administration. All
#  rights reserved.
#
#  The NGT platform is licensed under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance with the
#  License. You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# __END_LICENSE__

# Run bundle adjustment, stereo, generate DEMs, merge dems, perform alignment, etc,
# for a series of icebridge images

import os, sys, optparse, datetime, logging, multiprocessing

# The path to the ASP python files
basepath      = os.path.dirname(os.path.realpath(__file__))  # won't change, unlike syspath
pythonpath    = os.path.abspath(basepath + '/../Python')     # for dev ASP
libexecpath   = os.path.abspath(basepath + '/../libexec')    # for packaged ASP
binpath       = os.path.abspath(basepath + '/../bin')        # for packaged ASP
icebridgepath = os.path.abspath(basepath + '/../IceBridge')  # IceBridge tools
toolspath     = os.path.abspath(basepath + '/../Tools')      # ASP Tools
sys.path.insert(0, basepath) # prepend to Python path
sys.path.insert(0, pythonpath)
sys.path.insert(0, libexecpath)
sys.path.insert(0, icebridgepath)
sys.path.insert(0, libexecpath)
sys.path.insert(0, toolspath)

import icebridge_common
import asp_system_utils, asp_alg_utils, asp_geo_utils
asp_system_utils.verify_python_version_is_supported()

# Prepend to system PATH
os.environ["PATH"] = libexecpath + os.pathsep + os.environ["PATH"]
os.environ["PATH"] = toolspath   + os.pathsep + os.environ["PATH"]
os.environ["PATH"] = binpath     + os.pathsep + os.environ["PATH"]


def consolidateGeodiffResults(inputFiles, outputPath=None):
    '''Create a summary file of multiple geodiff csv output files'''

    if len(inputFiles) == 0: # No input files, do nothing.
        return None

    # Take the max/min of min/max and the mean of mean and stddev
    keywords = ['Max', 'Min', 'Mean', 'StdDev']
    mergedResult = {'Max':-999999.0, 'Min':999999.0, 'Mean':0.0, 'StdDev':0.0}
    for csvPath in inputFiles:
        results = icebridge_common.readGeodiffOutput(csvPath)
        if results['Max'] > mergedResult['Max']:
            mergedResult['Max'] = results['Max']
        if results['Min'] < mergedResult['Min']:
            mergedResult['Min'] = results['Min']
        mergedResult['Mean'  ] += results['Mean'  ]
        mergedResult['StdDev'] += results['StdDev']
    mergedResult['Mean'  ] = mergedResult['Mean'  ] / float(len(inputFiles))
    mergedResult['StdDev'] = mergedResult['StdDev'] / float(len(inputFiles))
    
    # If an output path was provided, write out the values in a similar to geodiff format.
    if outputPath:
        with open(outputPath, 'w') as f:
            f.write('# Max difference:       '+str(mergedResult['Max'   ])+'\n')
            f.write('# Min difference:       '+str(mergedResult['Min'   ])+'\n')
            f.write('# Mean difference:      '+str(mergedResult['Mean'  ])+'\n')
            f.write('# StdDev of difference: '+str(mergedResult['StdDev'])+'\n')
    
    return mergedResult
            

def createDem(i, options, inputPairs, prefixes, demFiles, projString, 
              threadText, suppressOutput, redo, logger):
    '''Create a DEM from a pair of images'''

    # Since we use epipolar alignment our images should be aligned at least this well.
    VERTICAL_SEARCH_LIMIT = 10
    
    # Get the appropriate image to use as a stereo pair    
    pairIndex = i + options.stereoImageInterval

    thisPairPrefix = prefixes[i]
    argString      = ('%s %s %s %s ' % (inputPairs[i][0],  inputPairs[pairIndex][0], 
                                        inputPairs[i][1],  inputPairs[pairIndex][1]))

    # Testing: Is there any performance hit from using --corr-seed-mode 0 ??
    #          This skips D_sub creation and saves processing time.
    stereoCmd = ('stereo %s %s -t nadirpinhole --alignment-method epipolar %s --corr-seed-mode 0 --epipolar-threshold 20' %
                 (argString, thisPairPrefix, threadText))
    searchLimitString = (' --corr-search-limit -9999 -' + str(VERTICAL_SEARCH_LIMIT) +
                         ' 9999 ' + str(VERTICAL_SEARCH_LIMIT) )
    if '--stereo-algorithm 0' not in options.stereoArgs:
        correlationArgString = (' --xcorr-threshold 2 --min-xcorr-level 1 --corr-kernel 7 7 ' 
                                + ' --corr-tile-size 9000 --cost-mode 4 --sgm-search-buffer 4 1 '
                                + searchLimitString + ' --corr-memory-limit-mb 16000 '
                                + options.stereoArgs
                               )
        #+ ' --corr-blob-filter 100')
        filterArgString = (' --rm-cleanup-passes 0 --median-filter-size 5 ' +
                           ' --texture-smooth-size 17 --texture-smooth-scale 0.14 ')
    else:
        correlationArgString = options.stereoArgs
        filterArgString = ''
        
    stereoCmd += correlationArgString
    stereoCmd += filterArgString

    triOutput = thisPairPrefix + '-PC.tif'
    asp_system_utils.executeCommand(stereoCmd, triOutput, suppressOutput, redo)

#     # temporary!!!
#     cmd = ('cp tmp.txt tmp2.txt') 
#     p2dOutput = 'tm'
#     asp_system_utils.executeCommand(cmd, p2dOutput, suppressOutput, redo)

    # point2dem on the result of ASP
    # - The size limit is to prevent bad point clouds from creating giant DEM files which
    #   cause the processing node to crash.
    cmd = ('point2dem --max-output-size 10000 10000 --tr %lf --t_srs %s %s %s --errorimage' 
           % (options.demResolution, projString, triOutput, threadText))
    p2dOutput = demFiles[i]
    asp_system_utils.executeCommand(cmd, p2dOutput, suppressOutput, redo)

    # COLORMAP
    #colormapMin = -20 # To really be useful we need to read the range off the
    # lidar and use it for all files.
    #colormapMax =  20
    colorOutput = thisPairPrefix+'-DEM_CMAP.tif'
    #cmd = ('colormap --min %f --max %f %s -o %s'
    #     % (colormapMin, colormapMax, p2dOutput, colorOutput))
    cmd = ('colormap %s -o %s'  
           % (p2dOutput, colorOutput))
    asp_system_utils.executeCommand(cmd, colorOutput, suppressOutput, redo)
    
def main(argsIn):

    try:
        usage = '''usage: process_icebridge_batch <imageA> <imageB> [imageC ...] <cameraA> <cameraB> [cameraC ...]'''
        
        parser = optparse.OptionParser(usage=usage)

        # Data options
        parser.add_option('--south', action='store_true', default=False, dest='isSouth',  
                          help='MUST be set if the images are in the southern hemisphere.')
                          
        parser.add_option('--lidar-folder', default=None, dest='lidarFolder',  
                          help='Use pc-align to match the closest lidar file.')

        parser.add_option('--output-folder', default=None, dest='outputFolder',  
                          help='The folder used for output.')

        parser.add_option('--reference-dem', default=None, dest='referenceDem',  
                          help='Low resolution DEM used for certain checks.')
                          
        parser.add_option('--fireball-folder', default=None, dest='fireballFolder',
                          help='Folder containing fireball DEMs.')

        # Processing options
        parser.add_option('--max-displacement', dest='maxDisplacement', default=20,
                          type='float', help='Max displacement value passed to pc_align.')

        parser.add_option('--solve-intrinsics', action='store_true', default=False,
                          dest='solve_intr',  
                          help='If to float the intrinsics params.')

        parser.add_option('--stereo-arguments', dest='stereoArgs', default='',
                          help='Additional argument string to be passed to the stereo command.')
                          
        parser.add_option('--stereo-image-interval', dest='stereoImageInterval', default=1,
                          type='int', help='Advance this many frames to get the stereo pair. ' + \
                          ' Also sets bundle adjust overlap limit.')

        # Output options
        parser.add_option('--lidar-overlay', action='store_true', default=False,
                          dest='lidarOverlay',  
                          help='Generate a lidar overlay for debugging.')

        parser.add_option('--dem-resolution', dest='demResolution', default=0.4,
                          type='float', help='Generate output DEMs at this resolution.')

        # Performance options
        parser.add_option('--num-threads', dest='numThreads', default=None,
                          type='int', help='The number of threads to use for processing.')

        parser.add_option('--num-processes-per-batch', dest='numProcessesPerBatch', default=1,
                          type='int', help='The number of simultaneous processes to run ' + \
                          'for each batch. This better be kept at 1 if running more than one batch.')

        (options, args) = parser.parse_args(argsIn)

        # Check argument count
        numArgs    = len(args)
        numCameras = (numArgs) / 2
        if ( (2*numCameras - numArgs) != 0) or (numCameras < 2):
            print("Expecting as many images as cameras. Got: " + " ".join(args))
            print usage
            return 0

        # Verify all input files exist
        for i in range(0,numArgs):
            if not os.path.exists(args[i]):
                print 'Input file '+ args[i] +' does not exist!'
                return 0

        # Parse input files
        inputPairs   = []
        for i in range(0, numCameras):
            image  = args[i]
            camera = args[i + numCameras]
            inputPairs.append([image, camera])
        imageCameraString = ' '.join(args)
       
        #print 'Read input pairs: ' + str(inputPairs)

    except optparse.OptionError, msg:
        raise Usage(msg)

    projString = icebridge_common.getProjString(options.isSouth, addQuotes=True)
       
    if not os.path.exists(options.outputFolder):
        os.mkdir(options.outputFolder)

    #logger = logging.getLogger(__name__)
    logLevel = logging.INFO # Make this an option??
    logger = icebridge_common.setUpLogger(options.outputFolder, logLevel, 'icebridge_batch_log')

    suppressOutput = False
    redo           = False

    logger.info('Starting processing...')


    # Check that the output GSD is not set too much lower than the native resolution
    if options.referenceDem:
        MAX_OVERSAMPLING = 2.0
        computedGsd = options.demResolution
        try:
            # Compute the native GSD of the first input camera
            computedGsd = icebridge_common.getCameraGsdRetry(inputPairs[0][0], inputPairs[0][1], 
                                                             logger, options.referenceDem, projString)
            
            print 'GSD = ' + str(computedGsd)
        except:
            logger.warning('Failed to compute GSD for camera: ' + inputPairs[0][1])
        if options.demResolution < (computedGsd*MAX_OVERSAMPLING):
            logger.warning('Specified resolution ' + str(options.demResolution) + 
                           ' is too fine for camera with computed GSD ' + str(computedGsd) +
                           '.  Switching to native GSD.)')
            options.demResolution = computedGsd
        # Undersampling is not as dangerous, just print a warning.
        if options.demResolution > 5*computedGsd:
            logger.warning('Specified resolution ' + str(options.demResolution) + 
                           ' is much larger than computed GSD ' + str(computedGsd))
                           

    # If a lidar folder was specified, find the best lidar file.
    lidarFile = None
    if options.lidarFolder:
        logger.info('Searching for matching lidar file...')
        lidarFile = icebridge_common.findMatchingLidarFile(inputPairs[0][0], options.lidarFolder)
        logger.info('Found matching lidar file ' + lidarFile)
        lidarCsvFormatString = icebridge_common.getLidarCsvFormat(lidarFile)
   
    outputPrefix  = os.path.join(options.outputFolder, 'out')
         
    threadText = ''
    if options.numThreads:
        threadText = ' --threads ' + str(options.numThreads) +' '
    
    # BUNDLE_ADJUST
    # - Bundle adjust all of the input images in the batch at the same time.
    # - An overlap number less than 2 is prone to very bad bundle adjust results so
    #   don't use less than that.  If there is really only enough overlap for one we
    #   will have to examine the results very carefully!
    MIN_BA_OVERLAP = 2
    CAMERA_WEIGHT  = 0.1   # TODO: Find the best value here
    ROBUST_THRESHOLD = 2.0 # TODO: Find the best value here
    OVERLAP_EXPONENT = 0   # TODO: Find the best value here
    
    bundlePrefix   = os.path.join(options.outputFolder, 'bundle/out')
    baOverlapLimit = options.stereoImageInterval + 3
    if baOverlapLimit < MIN_BA_OVERLAP:
        baOverlapLimit = MIN_BA_OVERLAP
        
    cmd = (('bundle_adjust %s -o %s %s --datum wgs84 --camera-weight %0.16g -t nadirpinhole ' +
           '--local-pinhole --overlap-limit %d --robust-threshold %0.16g ' +
            '--overlap-exponent %0.16g --epipolar-threshold 20')  \
           % (imageCameraString, bundlePrefix, threadText, CAMERA_WEIGHT, baOverlapLimit,
              ROBUST_THRESHOLD, OVERLAP_EXPONENT))
    
    if options.solve_intr:
        cmd += ' --solve-intrinsics'
    
    # Point to the new camera models
    for pair in inputPairs:       
        newCamera = bundlePrefix +'-'+ os.path.basename(pair[1])
        pair[1] = newCamera
        imageCameraString.replace(pair[1], newCamera)
    # Run the BA command
    asp_system_utils.executeCommand(cmd, newCamera, suppressOutput, redo)

    # Generate a map of initial camera positions
    orbitvizAfter = os.path.join(options.outputFolder, 'cameras_out.kml')
    vizString  = ''
    for (image, camera) in inputPairs: 
        vizString += image + ' ' + camera + ' '
    cmd = ('orbitviz --hide-labels -t nadirpinhole -r wgs84 -o ' +
           orbitvizAfter + ' '+ vizString)
    asp_system_utils.executeCommand(cmd, orbitvizAfter, suppressOutput, redo)

    # STEREO
    
    # Call stereo seperately on each pair of cameras and create a DEM
    numRuns = numCameras - options.stereoImageInterval

    prefixes = []
    demFiles = []
    atLeastOneDemMissing = False
    for i in range(0, numRuns):
        thisPairPrefix = os.path.join(options.outputFolder, 'stereo_pair_'+str(i)+'/out')
        prefixes.append(thisPairPrefix)
        p2dOutput = thisPairPrefix + '-DEM.tif'
        demFiles.append(p2dOutput)
        if not os.path.exists(p2dOutput):
            atLeastOneDemMissing = True
        

    # We can either process the batch serially, or in parallel For
    # many batches the former is preferred, with the batches
    # themselves being in parallel.
    if options.numProcessesPerBatch > 1:
        logger.info('Starting processing pool for given batch with ' +
                    str(options.numProcessesPerBatch) + ' processes.')
        pool = multiprocessing.Pool(options.numProcessesPerBatch)
        taskHandles = []
        for i in range(0, numRuns):
            taskHandles.append(pool.apply_async(createDem, 
                                                (i, options, inputPairs, prefixes, demFiles,
                                                 projString, threadText, suppressOutput, redo,
                                                 logger)))
        # Wait for all the tasks to complete
        icebridge_common.waitForTaskCompletionOrKeypress(taskHandles, logger, interactive = False, 
                                                         quitKey='q', sleepTime=20)
        
        # Either all the tasks are finished or the user requested a cancel.
        # Clean up the processing pool
        icebridge_common.stopTaskPool(pool)

    else:
        for i in range(0, numRuns):
            createDem(i, options, inputPairs, prefixes, demFiles, projString, threadText,
                   suppressOutput, redo, logger)

    # If we had to create at least one DEM, need to redo all the post-DEM creation steps
    if atLeastOneDemMissing:
        redo = True

    #raise Exception('BA DEBUG')
    logger.info('Finished running all stereo instances.')
    
    numDems = len(demFiles)


    # Check the elevation disparities between the DEMS.  High disparity
    #  usually means there was an alignment error.
    INTER_DEM_DIFF_CUTOFF = 1.0 # Meters
    FIREBALL_DIFF_CUTOFF  = 1.0 # Meters
    interDiffSummaryPath =  outputPrefix + '_inter_diff_summary.csv'
    interDiffCsvPaths    = []
    
    for i in range(1,numDems):
        try:
            # Call geodiff
            prefix  = outputPrefix + '_inter_dem_' + str(i)
            csvPath = prefix + "-diff.tif"
            cmd = ('geodiff --absolute %s %s -o %s' % (demFiles[0], demFiles[i], prefix))
            logger.info(cmd)
            asp_system_utils.executeCommand(cmd, csvPath, suppressOutput, redo)
            
            # Read in and examine the results
            results = icebridge_common.readGeodiffOutput(csvPath)
            interDiffCsvPaths.append(csvPath)
            if abs(results['Mean']) > INTER_DEM_DIFF_CUTOFF:
                logger.warning('Difference between dem ' + demFiles[0] + ' and dem ' + demFiles[i]
                               + ' is large: ' + str(results['Mean']))
        except:
            pass # Files with no overlap will fail here
            #logger.warning('Difference between dem ' + demFiles[0] + ' and dem ' + demFiles[i] + ' failed!')

    # Can do interdiff only if there is more than one DEM
    if numDems > 1:
        consolidateGeodiffResults(interDiffCsvPaths, interDiffSummaryPath)
    else:
        logger.info("Only one stereo pair is present, cannot create: " + interDiffSummaryPath)
        
    # DEM_MOSAIC
    allDemPath = outputPrefix + '-DEM.tif'
    if numDems == 1:
        # If there are only two files just skip this step
        icebridge_common.makeSymLink(demFiles[0], allDemPath)
    else:
        demString = ' '.join(demFiles)
        # Only the default blend method produces good results but the DEMs must not be too 
        #  far off for it to work.
        print projString
        cmd = ('dem_mosaic %s --tr %lf --t_srs %s %s -o %s' 
               % (demString, options.demResolution, projString, threadText, outputPrefix))
        print cmd
        mosaicOutput = outputPrefix + '-tile-0.tif'
        asp_system_utils.executeCommand(cmd, mosaicOutput, suppressOutput, redo)
        
        # Create a symlink to the mosaic file with a better name
        icebridge_common.makeSymLink(mosaicOutput, allDemPath)


    # Optional visualization of the LIDAR file
    if options.lidarOverlay and lidarFile:
        LIDAR_DEM_RESOLUTION     = 5
        LIDAR_PROJ_BUFFER_METERS = 100
    
        # Get buffered projection bounds of this image so it is easier to compare the LIDAR
        #demGeoInfo = asp_geo_utils.getImageGeoInfo(p2dOutput, getStats=False)
        demGeoInfo = asp_geo_utils.getImageGeoInfo(allDemPath, getStats=False)
        projBounds = demGeoInfo['projection_bounds']
        minX = projBounds[0] - LIDAR_PROJ_BUFFER_METERS # Expand the bounds a bit
        minY = projBounds[2] - LIDAR_PROJ_BUFFER_METERS
        maxX = projBounds[1] + LIDAR_PROJ_BUFFER_METERS
        maxY = projBounds[3] + LIDAR_PROJ_BUFFER_METERS

        # Generate a DEM from the lidar point cloud in this region        
        lidarDemPrefix = os.path.join(options.outputFolder, 'cropped_lidar')
        cmd = ('point2dem --max-output-size 10000 10000 --t_projwin %f %f %f %f --tr %lf --t_srs %s %s %s --csv-format %s -o %s' 
               % (minX, minY, maxX, maxY,
                  LIDAR_DEM_RESOLUTION, projString, lidarFile, threadText, 
                  lidarCsvFormatString, lidarDemPrefix))
        lidarDemOutput = lidarDemPrefix+'-DEM.tif'
        asp_system_utils.executeCommand(cmd, lidarDemOutput, suppressOutput, redo)
            
        colorOutput = lidarDemPrefix+'-DEM_CMAP.tif'
        cmd = ('colormap  %s -o %s' 
               % ( lidarDemOutput, colorOutput))
        #cmd = ('colormap --min %f --max %f %s -o %s'
        #       % (colormapMin, colormapMax, lidarDemOutput, colorOutput))
        asp_system_utils.executeCommand(cmd, colorOutput, suppressOutput, redo)

    # Compare to Fireball DEMs if available                           
    if options.fireballFolder:
        # Get the fireball DEM for each input image
        # - Note that each fireball DEM has input from 3+ images so this is an approximation.
        images, cameras      = zip(*inputPairs)
        fireballDems         = icebridge_common.getTifs(options.fireballFolder, prependFolder=True)
        matchingFireballDems = icebridge_common.getMatchingFrames(images, fireballDems)

        fireballDiffSummaryPath  =  outputPrefix + '_fireball_diff_summary.csv'
        fireLidarDiffSummaryPath =  outputPrefix + '_fireLidar_diff_summary.csv'
        fireballDiffCsvPaths     = []
        fireLidarDiffCsvPaths    = []
    
        # Loop through matches
        # - Each fireball DEM is compared to our final output DEM as without the pc_align step
        #   the errors will be high and won't mean much.
        for i in range(0,numDems):
            dem = demFiles[i]
            fireball = matchingFireballDems[i]
            if not fireball: # Skip missing fireball file
                continue
            #try:
            prefix  = outputPrefix + '_fireball_' + str(i)
            csvPath = prefix + "-diff.tif"
            cmd = ('geodiff --absolute %s %s -o %s' % (allDemPath, fireball, prefix))
            logger.info(cmd)
            asp_system_utils.executeCommand(cmd, csvPath, suppressOutput, redo)
            
            results = icebridge_common.readGeodiffOutput(csvPath)
            fireballDiffCsvPaths.append(csvPath)
            if abs(results['Mean']) > FIREBALL_DIFF_CUTOFF:
                logger.warning('Difference between dem ' + demFiles[i]
                               + ' and fireball is large: ' + str(results['Mean']))
    
            # If the lidar file is also available, compare Fireball to lidar.
            if lidarFile:                           
                prefix  = outputPrefix + '_fireball_lidar_' + str(i)
                csvPath = prefix + "-diff.csv"
                cmd = ('geodiff --absolute --csv-format %s %s %s -o %s' % 
                       (lidarCsvFormatString, fireball, lidarFile, prefix))
                logger.info(cmd)
                asp_system_utils.executeCommand(cmd, csvPath, suppressOutput, redo)
                fireLidarDiffCsvPaths.append(csvPath)
    
                               
            #except:
            #    logger.warning('Difference between dem ' + demFiles[0] + ' and fireball failed!')
        consolidateGeodiffResults(fireballDiffCsvPaths,  fireballDiffSummaryPath )
        consolidateGeodiffResults(fireLidarDiffCsvPaths, fireLidarDiffSummaryPath)

    if lidarFile:
        # PC_ALIGN
        alignPrefix = os.path.join(options.outputFolder, 'align/out')
        alignOptions = ( ('--max-displacement %f --csv-format %s ' +   \
                          '--save-inv-transformed-reference-points') % \
                         (options.maxDisplacement, lidarCsvFormatString))
        cmd = ('pc_align %s %s %s -o %s %s' %
               (alignOptions, allDemPath, lidarFile, alignPrefix, threadText))
        alignOutput = alignPrefix+'-trans_reference.tif'
        asp_system_utils.executeCommand(cmd, alignOutput, suppressOutput, redo)
        
        # POINT2DEM on the aligned PC file
        cmd = ('point2dem --tr %lf --t_srs %s %s %s --errorimage' 
               % (options.demResolution, projString, alignOutput, threadText))
        p2dOutput = alignPrefix+'-trans_reference-DEM.tif'
        asp_system_utils.executeCommand(cmd, p2dOutput, suppressOutput, redo)

        # Create a symlink to the DEM in the main directory
        demSymlinkPath = outputPrefix + '-align-DEM.tif'
        icebridge_common.makeSymLink(p2dOutput, demSymlinkPath)
        allDemPath = demSymlinkPath

        cmd = ('geodiff --absolute --csv-format %s %s %s -o %s' % \
               (lidarCsvFormatString, allDemPath, lidarFile, outputPrefix))
        logger.info(cmd)
        asp_system_utils.executeCommand(cmd, outputPrefix + "-diff.csv", suppressOutput, redo)

                           


    # HILLSHADE
    hillOutput = outputPrefix+'-DEM_HILLSHADE.tif'
    cmd = 'hillshade ' + allDemPath +' -o ' + hillOutput
    asp_system_utils.executeCommand(cmd, hillOutput, suppressOutput, redo)

    # Generate a low resolution compressed thumbnail of the hillshade for debugging
    thumbOutput = outputPrefix + '-DEM_HILLSHADE_browse.tif'
    cmd = 'gdal_translate '+hillOutput+' '+thumbOutput+' -of GTiff -outsize 10% 10% -b 1 -co "COMPRESS=JPEG"'
    asp_system_utils.executeCommand(cmd, thumbOutput, suppressOutput, redo)

    # COLORMAP
    #colormapMin = -10 # TODO: Automate these?
    #colormapMax =  20
    colorOutput = outputPrefix+'-DEM_CMAP.tif'
    #cmd = ('colormap --min %f --max %f %s -o %s' 
    #       % (colormapMin, colormapMax, allDemPath, colorOutput))
    cmd = ('colormap  %s -o %s' 
           % (allDemPath, colorOutput))
    asp_system_utils.executeCommand(cmd, colorOutput, suppressOutput, redo)


    logger.info('Finished!')

# Run main function if file used from shell
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

