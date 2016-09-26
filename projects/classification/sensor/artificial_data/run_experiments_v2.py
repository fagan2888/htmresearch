#!/usr/bin/env python
# ----------------------------------------------------------------------
# Numenta Platform for Intelligent Computing (NuPIC)
# Copyright (C) 2016, Numenta, Inc.  Unless you have an agreement
# with Numenta, Inc., for a separate license for this software code, the
# following terms and conditions apply:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU Affero Public License for more details.
#
# You should have received a copy of the GNU Affero Public License
# along with this program.  If not, see http://www.gnu.org/licenses.
#
# http://numenta.org/licenses/
# ----------------------------------------------------------------------

import csv
import json
import logging
import numpy as np
import simplejson

from htmresearch.frameworks.clustering.sdr_clustering import Clustering
from nupic.data.file_record_stream import FileRecordStream

from htmresearch.frameworks.clustering.distances import interClusterDistances
from htmresearch.frameworks.classification.network_factory import (
  configureNetwork, getNetworkRegions)
from htmresearch.frameworks.classification.utils.sensor_data import (
  generateSensorData, plotSensorData, cleanTitle)
from htmresearch.frameworks.classification.utils.network_config import (
  generateSampleNetworkConfig)
from htmresearch.frameworks.classification.utils.traces import plotTraces

from settings import (NUM_CATEGORIES,
                      NUM_PHASES,
                      NUM_REPS,
                      SIGNAL_TYPES,
                      WHITE_NOISE_AMPLITUDES,
                      SIGNAL_AMPLITUDES,
                      SIGNAL_MEANS,
                      DATA_DIR,
                      NOISE_LENGTHS,
                      EXP_SETUPS_OUTPUT_FILE,
                      TRACES_OUTPUT_FILE,
                      USE_REAL_DATA,
                      INPUT_FILE,
                      NUM_POINTS,
                      USE_CONFIG_TEMPLATE,
                      PLOT)

from clustering_settings import (startClusteringIndex,
                                 mergeThreshold,
                                 anomalousThreshold,
                                 stableThreshold,
                                 minClusterSize,
                                 similarityThreshold,
                                 pruningFrequency,
                                 pruneClusters,
                                 rollingAccuracyWindow)

_LOGGER = logging.getLogger()
_LOGGER.setLevel(logging.DEBUG)



def initTrace():
  trace = {
    'recordNumber': [],
    'sensorValue': [],
    'actualCategory': [],
    'tmActiveCells': [],
    'tmPredictedActiveCells': [],
    'anomalyScore': [],
    'tpActiveCells': [],
    'classificationInference': [],
    'classificationAccuracy': [],
    'clusteringInference': [],
    'predictedClusterId': [],
    'clusteringAccuracy': [],
    'clusterHomogeneity': [],
    'clusteringConfidence': []
  }

  return trace



def onlineRollingAccuracy(trace,
                          rollingWindowSize,
                          inferenceFieldName,
                          accuracyFieldName,
                          ignoreNoise=True):
  """
  Online computation of moving average.
  From: http://www.daycounter.com/LabBook/Moving-Average.phtml
  """
  if len(trace[accuracyFieldName]) > 0:
    ma = trace[accuracyFieldName][-1]
    if trace[inferenceFieldName][-1] == trace['actualCategory'][-1]:
      x = 1
    else:
      x = 0

    if ignoreNoise and trace['actualCategory'][-1] > 0:
      ma += float(x - ma) / rollingWindowSize

  else:
    ma = 0

  return ma



def updateTrace(trace,
                recordNumber,
                sensorValue,
                actualCategory,
                tmActiveCells,
                tmPredictedActiveCells,
                anomalyScore,
                tpActiveCells,
                classificationInference,
                classificationAccuracy,
                clusteringInference,
                predictedClusterId,
                clusteringAccuracy,
                clusterHomogeneity,
                clusteringConfidence):
  trace['recordNumber'].append(recordNumber)
  trace['sensorValue'].append(sensorValue)
  trace['actualCategory'].append(actualCategory)
  trace['tmActiveCells'].append(tmActiveCells)
  trace['tmPredictedActiveCells'].append(tmPredictedActiveCells)
  trace['anomalyScore'].append(anomalyScore)
  trace['tpActiveCells'].append(tpActiveCells)
  trace['classificationInference'].append(classificationInference)
  trace['classificationAccuracy'].append(classificationAccuracy)
  trace['clusteringInference'].append(clusteringInference)
  trace['predictedClusterId'].append(predictedClusterId)
  trace['clusteringAccuracy'].append(clusteringAccuracy)
  trace['clusterHomogeneity'].append(clusterHomogeneity)
  trace['clusteringConfidence'].append(clusteringConfidence)
  return trace



def outputTraceInfo(recordNumber,
                    sensorValue,
                    actualCategory,
                    anomalyScore,
                    classificationInference,
                    classificationAccuracy,
                    clusteringInference,
                    predictedClusterId,
                    clusteringAccuracy,
                    clusterHomogeneity,
                    clusteringConfidence,
                    numClusters):
  # Network
  _LOGGER.debug('-> recordNumber: %s' % recordNumber)
  _LOGGER.debug('-> sensorValue: %s' % sensorValue)
  _LOGGER.debug('-> actualCategory: %s' % actualCategory)
  _LOGGER.debug('-> anomalyScore: %s' % anomalyScore)

  # Classification
  _LOGGER.debug('-> classificationInference: %s' % classificationInference)
  _LOGGER.debug('-> classificationAccuracy: %s / 1' % classificationAccuracy)

  # Clustering
  _LOGGER.debug('-> clusteringInference: %s' % clusteringInference)
  _LOGGER.debug('-> predictedClusterId: %s' % predictedClusterId)
  _LOGGER.debug('-> clusteringAccuracy: %s / 1' % clusteringAccuracy)
  _LOGGER.debug('-> clusterHomogeneity: %s / 100' % clusterHomogeneity)
  _LOGGER.debug('-> clusteringConfidence: %s' % clusteringConfidence)
  _LOGGER.debug('-> numClusters: %s' % numClusters)
  _LOGGER.debug('---')



def outputInterClusterDist(clustering):
  if _LOGGER.getEffectiveLevel() == logging.DEBUG:
    interClusterDist = interClusterDistances(clustering.getClusters(),
                                             clustering.getNewCluster())
    _LOGGER.debug('-> inter-cluster distances: %s' % interClusterDist)



def outputClustersStructure(clustering):
  if _LOGGER.getEffectiveLevel() == logging.DEBUG:
    labelClusters(clustering)

    # sort cluster-category frequencies by label and cumulative number of points
    sortedFreqDicts = sorted(
      clustering.clusterActualCategoriesFrequencies(),
      key=lambda x: (clustering.getClusterById(x['clusterId']).getLabel(),
                     sum([freq['numberOfPoints']
                          for freq in x['actualCategoryFrequencies']])))

    for frequencyDict in sortedFreqDicts:
      clusterId = frequencyDict['clusterId']
      actualCategoryFrequencies = frequencyDict['actualCategoryFrequencies']
      cluster = clustering.getClusterById(clusterId)
      _LOGGER.debug('-> frequencies of actual categories in cluster %s.'
                    % clusterId)
      _LOGGER.debug('-> cluster info: %s' % cluster)
      for freq in actualCategoryFrequencies:
        _LOGGER.debug('* actualCategory: %s' % freq['actualCategory'])
        _LOGGER.debug('* numberPoints: %s' % freq['numberOfPoints'])
        _LOGGER.debug('')



def getNetworkSetup(networkConfig):
  networkSetup = {}

  spEnabled = networkConfig['spRegionConfig'].get(
    'regionEnabled')
  tmEnabled = networkConfig['tmRegionConfig'].get(
    'regionEnabled')
  tpEnabled = networkConfig['tpRegionConfig'].get(
    'regionEnabled')
  classifierType = networkConfig['classifierRegionConfig'].get(
    'regionType')

  cells = networkConfig['tmRegionConfig']['regionParams']['cellsPerColumn']
  columns = networkConfig['tmRegionConfig']['regionParams']['columnCount']

  networkSetup['spEnabled'] = spEnabled
  networkSetup['tmEnabled'] = tmEnabled
  networkSetup['tpEnabled'] = tpEnabled
  networkSetup['classifierType'] = classifierType
  networkSetup['numTmCells'] = cells * columns
  return networkSetup



def generateExpId(filePath, networkSetup):
  baseName = filePath.split('/')[-1].split('.csv')[0]
  return '%s_sp=%s_tm=%s_tp=%s_%s' % (baseName,
                                      networkSetup['spEnabled'],
                                      networkSetup['tmEnabled'],
                                      networkSetup['tpEnabled'],
                                      networkSetup['classifierType'][3:-6])



def getClassifierInference(classifierRegion):
  """Return output categories from the classifier region."""
  if classifierRegion.type == 'py.KNNClassifierRegion':
    # The use of numpy.lexsort() here is to first sort by labelFreq, then
    # sort by random values; this breaks ties in a random manner.
    inferenceValues = classifierRegion.getOutputData('categoriesOut')
    randomValues = np.random.random(inferenceValues.size)
    return np.lexsort((randomValues, inferenceValues))[-1]
  else:
    return classifierRegion.getOutputData('categoriesOut')[0]



def convertNonZeroToSDR(patternNZs, sdrSize):
  sdrs = []
  for patternNZ in patternNZs:
    sdr = np.zeros(sdrSize)
    sdr[patternNZ] = 1
    sdrs.append(sdr)

  return sdrs



def runNetwork(networkConfig, expSetup):
  filePath = expSetup['inputFilePath']
  dataSource = FileRecordStream(streamID=filePath)
  network = configureNetwork(dataSource, networkConfig)

  (sensorRegion,
   spRegion,
   tmRegion,
   tpRegion,
   classifierRegion) = getNetworkRegions(network, networkConfig)

  trace = initTrace()

  clustering = Clustering(mergeThreshold,
                          anomalousThreshold,
                          stableThreshold,
                          minClusterSize,
                          similarityThreshold,
                          pruningFrequency)

  recordNumber = 0
  while 1:
    try:
      network.run(1)
      if recordNumber > startClusteringIndex:
        tmPredictedActiveCells = tmRegion.getOutputData('predictedActiveCells')
        tmPredictedActiveCells = tmPredictedActiveCells.astype(int)
        anomalyScore = tmRegion.getOutputData('anomalyScore')[0]
        actualCategory = sensorRegion.getOutputData('categoryOut')[0]
        (predictedCluster,
         clusteringConfidence) = clustering.cluster(recordNumber,
                                                    tmPredictedActiveCells,
                                                    anomalyScore,
                                                    actualCategory)
  
      else:
        predictedCluster = None
        clusteringConfidence = None
  
      (sensorValue,
       actualCategory,
       tmActiveCells,
       tmPredictedActiveCells,
       anomalyScore,
       tpActiveCells,
       classificationInference,
       classificationAccuracy,
       clusteringInference,
       predictedClusterId,
       clusteringAccuracy,
       clusterHomogeneity) = computeStats(trace,
                                          rollingAccuracyWindow,
                                          sensorRegion,
                                          tmRegion,
                                          tpRegion,
                                          classifierRegion,
                                          predictedCluster,
                                          clustering)
  
      trace = updateTrace(trace,
                          recordNumber,
                          sensorValue,
                          actualCategory,
                          tmActiveCells,
                          tmPredictedActiveCells,
                          anomalyScore,
                          tpActiveCells,
                          classificationInference,
                          classificationAccuracy,
                          clusteringInference,
                          predictedClusterId,
                          clusteringAccuracy,
                          clusterHomogeneity,
                          clusteringConfidence)
  
      if recordNumber % 50 == 0:
        outputTraceInfo(recordNumber,
                        sensorValue,
                        actualCategory,
                        anomalyScore,
                        classificationInference,
                        classificationAccuracy,
                        clusteringInference,
                        predictedClusterId,
                        clusteringAccuracy,
                        clusterHomogeneity,
                        clusteringConfidence,
                        len(clustering.getClusters()))
      recordNumber += 1
    except StopIteration:
      print "Data streaming completed!"
      break

  outputClustersStructure(clustering)
  outputInterClusterDist(clustering)
  return trace



def computeStats(trace,
                 rollingAccuracyWindow,
                 sensorRegion,
                 tmRegion,
                 tpRegion,
                 classifierRegion,
                 predictedCluster,
                 clustering):
  sensorValue = sensorRegion.getOutputData('sourceOut')[0]
  actualCategory = sensorRegion.getOutputData('categoryOut')[0]

  if tmRegion:
    tmPredictedActiveCells = tmRegion.getOutputData(
      'predictedActiveCells').astype(int)
    tmActiveCells = tmRegion.getOutputData('activeCells').astype(int)
    anomalyScore = tmRegion.getOutputData('anomalyScore')[0]
  else:
    tmActiveCells = None
    tmPredictedActiveCells = None
    anomalyScore = None

  if tpRegion:
    tpActiveCells = tpRegion.getOutputData('mostActiveCells')
    tpActiveCells = tpActiveCells.nonzero()[0]
  else:
    tpActiveCells = None

  classificationInference = getClassifierInference(classifierRegion)
  classificationAccuracy = onlineRollingAccuracy(trace,
                                                 rollingAccuracyWindow,
                                                 'classificationInference',
                                                 'classificationAccuracy')

  (clusteringInference,
   predictedClusterId,
   clusterHomogeneity) = getClusteringInference(predictedCluster, clustering)
  clusteringAccuracy = onlineRollingAccuracy(trace,
                                             rollingAccuracyWindow,
                                             'clusteringInference',
                                             'clusteringAccuracy')

  return (sensorValue,
          actualCategory,
          tmActiveCells,
          tmPredictedActiveCells,
          anomalyScore,
          tpActiveCells,
          classificationInference,
          classificationAccuracy,
          clusteringInference,
          predictedClusterId,
          clusteringAccuracy,
          clusterHomogeneity)



def getClusteringInference(predictedCluster, clustering):
  if predictedCluster:
    predictedClusterId = predictedCluster.getId()
    labelClusters(clustering)
    clusteringInference = predictedCluster.getLabel()
  else:
    clusteringInference = None
    predictedClusterId = None

  clusterHomogeneity = computeClusterHomogeneity(clustering)

  return clusteringInference, predictedClusterId, clusterHomogeneity



def computeClusterHomogeneity(clustering):
  numCorrect = 0
  numPoints = 0
  for cluster in clustering.getClusters():
    for point in cluster.getPoints():
      if point.getLabel() == cluster.getLabel():
        numCorrect += 1
      numPoints += 1
  if numPoints > 0:
    return 100.0 * numCorrect / numPoints
  else:
    return 0.0



def labelClusters(clustering):
  for frequencyDict in clustering.clusterActualCategoriesFrequencies():
    actualCategoryFrequencies = frequencyDict['actualCategoryFrequencies']
    clusterId = frequencyDict['clusterId']
    cluster = clustering.getClusterById(clusterId)
    highToLowFreqs = sorted(actualCategoryFrequencies,
                            key=lambda x: -x['numberOfPoints'])
    bestCategory = highToLowFreqs[0]['actualCategory']
    cluster.setLabel(bestCategory)



def runExperiments(networkConfig):
  expResults = []
  for signalType in SIGNAL_TYPES:
    for noiseAmplitude in WHITE_NOISE_AMPLITUDES:
      for signalMean in SIGNAL_MEANS:
        for signalAmplitude in SIGNAL_AMPLITUDES:
          for numCategories in NUM_CATEGORIES:
            for numReps in NUM_REPS:
              for numPhases in NUM_PHASES:
                for noiseLengths in NOISE_LENGTHS:
                  _LOGGER.info('Exp #%s' % len(expResults))

                  (expSetup,
                   numPoints,
                   filePath) = generateSensorData(signalType,
                                                  DATA_DIR,
                                                  numPhases,
                                                  numReps,
                                                  signalMean,
                                                  signalAmplitude,
                                                  numCategories,
                                                  noiseAmplitude,
                                                  noiseLengths)
                  expResult = runExperiment(networkConfig,
                                            filePath,
                                            expSetup)
                  expResults.append(expResult)

  return expResults



def runExperiment(networkConfig, inputFilePath, expSetup=None):
  if expSetup is None:
    expSetup = {}

  expSetup['inputFilePath'] = inputFilePath
  networkTrace = runNetwork(networkConfig, expSetup)

  networkSetup = getNetworkSetup(networkConfig)
  expId = generateExpId(inputFilePath, networkSetup)
  expResult = {
    'expId': expId,
    'expTrace': networkTrace,
    'networkSetup': networkSetup,
    'expSetup': expSetup
  }

  return expResult



def saveExpSetups(outFile, expResults):
  """
  Save exp setups and final accuracy result to CSV file
  :param outFile: (str) path to CSV file where to save data
  :param expResults: (list of dict) experiment results
  """

  headers = expResults[0]['expSetup'].keys()
  headers.append('finalClassificationAccuracy')

  with open(outFile, 'wb') as fw:
    writer = csv.writer(fw)
    writer.writerow(headers)
    for i in range(len(expResults)):
      row = [expResults[i]['expSetup'][h] for h in headers[:-1]]
      row.append(expResults[i]['expTrace']['classificationAccuracy'][-1])
      writer.writerow(row)

    _LOGGER.info('Results saved to %s\n' % outFile)



def saveTraces(baseOutFile, expResults):
  """
  Save experiments network traces to CSV
  :param baseOutFile: (str) base name of the output file.
  :param expResults: (list of dict) experiment results
  """
  for expResult in expResults:
    expTrace = expResult['expTrace']
    numPoints = len(expTrace['recordNumber'])
    outFile = baseOutFile % expResult['expId']
    with open(outFile, 'wb') as f:
      writer = csv.writer(f)
      headers = expTrace.keys()
      writer.writerow(headers)
      for i in range(numPoints):
        row = []
        for t in expTrace.keys():
          if len(expTrace[t]) > i:
            if t in ['tmPredictedActiveCells', 'tmActiveCells']:
              row.append(json.dumps(list(expTrace[t][i].nonzero()[0])))
            elif type(expTrace[t][i]) == list:
              row.append(json.dumps(expTrace[t][i]))
            else:
              row.append(expTrace[t][i])
          else:
            row.append(None)
        writer.writerow(row)

    _LOGGER.info('traces saved to: %s' % outFile)



def plotExpTraces(expResults):
  for expResult in expResults:
    numPoints = len(expResult['expTrace']['recordNumber'])
    xlim = [0, numPoints]
    numTmCells = expResult['networkSetup']['numTmCells']
    traces = expResult['expTrace']
    title = cleanTitle(expResult['expSetup']['inputFilePath'])
    plotTraces(numTmCells, xlim, traces)



def run(expSetupOutputFile,
        tracesOutputFile,
        useRealData,
        inputFile,
        useConfigTemplate,
        plot):
  if useConfigTemplate:
    with open('config/network_config_template.json', 'rb') as jsonFile:
      templateNetworkConfig = simplejson.load(jsonFile)
      networkConfigurations = generateSampleNetworkConfig(templateNetworkConfig,
                                                          NUM_CATEGORIES)
  else:
    with open('config/knn_network_configs.json', 'rb') as jsonFile:
      networkConfigurations = simplejson.load(jsonFile)

  expResults = []
  for networkConfig in networkConfigurations:
    if useRealData:
      expResult = runExperiment(networkConfig, inputFile)
      expResults.append(expResult)
    else:
      expResults.extend(runExperiments(networkConfig))

  saveExpSetups(expSetupOutputFile, expResults)

  saveTraces(tracesOutputFile, expResults)

  if plot:
    plotSensorData([e['expSetup']['inputFilePath'] for e in expResults])
    plotExpTraces(expResults)



def main():
  run(EXP_SETUPS_OUTPUT_FILE,
      TRACES_OUTPUT_FILE,
      USE_REAL_DATA,
      INPUT_FILE,
      USE_CONFIG_TEMPLATE,
      PLOT)



if __name__ == '__main__':
  main()
