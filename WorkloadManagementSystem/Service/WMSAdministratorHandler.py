"""
This is a DIRAC WMS administrator interface.
It exposes the following methods:

Site mask related methods:
    setMask(<site mask>)
    getMask()

Access to the pilot data:
    getWMSStats()

"""

from types import DictType, ListType, IntType, LongType, StringTypes, FloatType
from tempfile import mkdtemp
import shutil

from DIRAC.Core.DISET.RequestHandler import RequestHandler
from DIRAC import gConfig, gLogger, S_OK, S_ERROR
from DIRAC.WorkloadManagementSystem.DB.JobDB import JobDB
from DIRAC.FrameworkSystem.Client.ProxyManagerClient       import gProxyManager
from DIRAC.WorkloadManagementSystem.DB.PilotAgentsDB import PilotAgentsDB
from DIRAC.WorkloadManagementSystem.DB.TaskQueueDB import TaskQueueDB
from DIRAC.WorkloadManagementSystem.Service.WMSUtilities import getPilotLoggingInfo, getWMSPilotOutput, getGridEnv
from DIRAC.Resources.Computing.ComputingElementFactory import ComputingElementFactory
import DIRAC.Core.Utilities.Time as Time
from DIRAC.ConfigurationSystem.Client.Helpers.Registry import getGroupOption, getUsernameForDN
from DIRAC.ConfigurationSystem.Client.Helpers.Resources import getQueue

__RCSID__ = "$Id$"

# This is a global instance of the database classes
jobDB = False
pilotDB = False
taskQueueDB = False

FINAL_STATES = ['Done','Aborted','Cleared','Deleted','Stalled']

def initializeWMSAdministratorHandler( serviceInfo ):
  """  WMS AdministratorService initialization
  """

  global jobDB
  global pilotDB
  global taskQueueDB

  jobDB = JobDB()
  pilotDB = PilotAgentsDB()
  taskQueueDB = TaskQueueDB()
  return S_OK()

class WMSAdministratorHandler(RequestHandler):

###########################################################################
  types_setSiteMask = [ListType]
  def export_setSiteMask( self, siteList ):
    """ Set the site mask for matching. The mask is given in a form of Classad string.
    """
    result = self.getRemoteCredentials()
    dn = result['DN']

    maskList = [ (site,'Active') for site in siteList ]
    result = jobDB.setSiteMask( maskList, dn, 'No comment' )
    return result

##############################################################################
  types_getSiteMask = []
  @staticmethod
  def export_getSiteMask(siteState = 'Active'):
    """ Get the site mask
    """
    return jobDB.getSiteMask( siteState )

  types_getSiteMaskStatus = []
  @staticmethod
  def export_getSiteMaskStatus(sites = None):
    """ Get the site mask of given site(s) with columns 'site' and 'status' only
    """

    return jobDB.getSiteMaskStatus( sites )

  types_getAllSiteMaskStatus = []
  @staticmethod
  def export_getAllSiteMaskStatus():
    """ Get all the site parameters in the site mask
    """
    return jobDB.getAllSiteMaskStatus()

##############################################################################
  types_banSite = [StringTypes]
  def export_banSite(self, site,comment='No comment'):
    """ Ban the given site in the site mask
    """

    result = self.getRemoteCredentials()
    dn = result['DN']
    result = getUsernameForDN(dn)
    if result['OK']:
      author = result['Value']
    else:
      author = dn
    result = jobDB.banSiteInMask(site, author, comment)
    return result

##############################################################################
  types_allowSite = [StringTypes]
  def export_allowSite(self,site,comment='No comment'):
    """ Allow the given site in the site mask
    """

    result = self.getRemoteCredentials()
    dn = result['DN']
    result = getUsernameForDN(dn)
    if result['OK']:
      author = result['Value']
    else:
      author = dn
    result = jobDB.allowSiteInMask(site,author,comment)
    return result

##############################################################################
  types_clearMask = []
  @staticmethod
  def export_clearMask():
    """ Clear up the entire site mask
    """

    return jobDB.removeSiteFromMask( None )

##############################################################################
  types_getSiteMaskLogging = [ list( StringTypes ) + [ListType] ]
  @staticmethod
  def export_getSiteMaskLogging( sites ):
    """ Get the site mask logging history
    """

    if type(sites) in StringTypes:
      sites = [sites]

    return jobDB.getSiteMaskLogging( sites )

##############################################################################
  types_getSiteMaskSummary = [ ]
  @staticmethod
  def export_getSiteMaskSummary():
    """ Get the mask status for all the configured sites
    """

    # Get all the configured site names
    result = gConfig.getSections('/Resources/Sites')
    if not result['OK']:
      return result
    grids = result['Value']
    sites = []
    for grid in grids:
      result = gConfig.getSections('/Resources/Sites/%s' % grid)
      if not result['OK']:
        return result
      sites += result['Value']

    # Get the current mask status
    result = jobDB.getSiteMaskStatus()
    siteDict = result['Value']
    for site in sites:
      if site not in siteDict:
        siteDict[site] = 'Unknown'

    return S_OK(siteDict)

##############################################################################
  types_getCurrentPilotCounters = [ DictType ]
  @staticmethod
  def export_getCurrentPilotCounters( attrDict={}):
    """ Get pilot counters per Status with attrDict selection. Final statuses are given for
        the last day.
    """

    result = pilotDB.getCounters( 'PilotAgents',['Status'], attrDict, timeStamp='LastUpdateTime')
    if not result['OK']:
      return result
    last_update = Time.dateTime() - Time.day
    resultDay = pilotDB.getCounters( 'PilotAgents',['Status'], attrDict, newer=last_update,
                                     timeStamp='LastUpdateTime')
    if not resultDay['OK']:
      return resultDay

    resultDict = {}
    for statusDict, count in result['Value']:
      status = statusDict['Status']
      resultDict[status] = count
      if status in FINAL_STATES:
        resultDict[status] = 0
        for statusDayDict,ccount in resultDay['Value']:
          if status == statusDayDict['Status']:
            resultDict[status] = ccount
          break

    return S_OK(resultDict)

##########################################################################################
  types_addPilotTQReference = [ ListType, [IntType, LongType], StringTypes, StringTypes ]
  @staticmethod
  def export_addPilotTQReference( pilotRef, taskQueueID, ownerDN, ownerGroup, broker='Unknown',
                                  gridType='DIRAC', requirements='Unknown',pilotStampDict={}):
    """ Add a new pilot job reference """
    return pilotDB.addPilotTQReference(pilotRef, taskQueueID,
                                       ownerDN, ownerGroup,
                                       broker, gridType, requirements,pilotStampDict)


  ##############################################################################
  types_getPilotOutput = [StringTypes]
  def export_getPilotOutput(self,pilotReference):
    """ Get the pilot job standard output and standard error files for the Grid
        job reference
    """

    return self.__getGridJobOutput(pilotReference)

  ##############################################################################
  types_getPilotInfo = [ list(StringTypes)+[ListType] ]
  @staticmethod
  def export_getPilotInfo(pilotReference):
    """ Get the info about a given pilot job reference
    """
    return pilotDB.getPilotInfo(pilotReference)

  ##############################################################################
  types_selectPilots = [ DictType ]
  @staticmethod
  def export_selectPilots(condDict):
    """ Select pilots given the selection conditions
    """
    return pilotDB.selectPilots(condDict)

  ##############################################################################
  types_storePilotOutput = [ StringTypes,StringTypes,StringTypes ]
  @staticmethod
  def export_storePilotOutput(pilotReference, output, error):
    """ Store the pilot output and error
    """
    return pilotDB.storePilotOutput(pilotReference,output,error)

  ##############################################################################
  types_getPilotLoggingInfo = [StringTypes]
  @staticmethod
  def export_getPilotLoggingInfo(pilotReference):
    """ Get the pilot logging info for the Grid job reference
    """

    result = pilotDB.getPilotInfo(pilotReference)
    if not result['OK'] or not result[ 'Value' ]:
      return S_ERROR('Failed to determine owner for pilot ' + pilotReference)

    pilotDict = result['Value'][pilotReference]
    owner = pilotDict['OwnerDN']
    group = pilotDict['OwnerGroup']
    gridType = pilotDict['GridType']

    return getPilotLoggingInfo( gridType, pilotReference, #pylint: disable=unexpected-keyword-arg
                                proxyUserDN = owner, proxyUserGroup = group )

  ##############################################################################
  types_getJobPilotOutput = [list( StringTypes ) + [ IntType, LongType]]
  def export_getJobPilotOutput(self,jobID):
    """ Get the pilot job standard output and standard error files for the DIRAC
        job reference
    """

    pilotReference = ''
    # Get the pilot grid reference first from the job parameters
    result = jobDB.getJobParameter( int( jobID ), 'Pilot_Reference' )
    if result['OK']:
      pilotReference = result['Value']

    if not pilotReference:
      # Failed to get the pilot reference, try to look in the attic parameters
      result = jobDB.getAtticJobParameters( int( jobID ), ['Pilot_Reference'] )
      if result['OK']:
        c = -1
        # Get the pilot reference for the last rescheduling cycle
        for cycle in result['Value']:
          if cycle > c:
            pilotReference = result['Value'][cycle]['Pilot_Reference']
            c = cycle

    if pilotReference:
      return self.__getGridJobOutput(pilotReference)
    else:
      return S_ERROR('No pilot job reference found')

  ##############################################################################
  @staticmethod
  def __getGridJobOutput(pilotReference):
    """ Get the pilot job standard output and standard error files for the Grid
        job reference
    """

    result = pilotDB.getPilotInfo(pilotReference)
    if not result['OK'] or not result[ 'Value' ]:
      return S_ERROR('Failed to get info for pilot ' + pilotReference)

    pilotDict = result['Value'][pilotReference]
    owner = pilotDict['OwnerDN']
    group = pilotDict['OwnerGroup']

    # FIXME: What if the OutputSandBox is not StdOut and StdErr, what do we do with other files?
    result = pilotDB.getPilotOutput(pilotReference)
    if result['OK']:
      stdout = result['Value']['StdOut']
      error = result['Value']['StdErr']
      if stdout or error:
        resultDict = {}
        resultDict['StdOut'] = stdout
        resultDict['StdErr'] = error
        resultDict['OwnerDN'] = owner
        resultDict['OwnerGroup'] = group
        resultDict['FileList'] = []
        return S_OK(resultDict)
      else:
        gLogger.warn( 'Empty pilot output found for %s' % pilotReference )

    gridType = pilotDict['GridType']
    if gridType == "gLite":
      result = getWMSPilotOutput( pilotReference, proxyUserDN = owner, proxyUserGroup = group) #pylint: disable=unexpected-keyword-arg
      if not result['OK']:
        return S_ERROR('Failed to get pilot output: '+result['Message'])
      # FIXME: What if the OutputSandBox is not StdOut and StdErr, what do we do with other files?
      stdout = result['StdOut']
      error = result['StdErr']
      fileList = result['FileList']
      if stdout:
        result = pilotDB.storePilotOutput(pilotReference,stdout,error)
        if not result['OK']:
          gLogger.error('Failed to store pilot output:',result['Message'])

      resultDict = {}
      resultDict['StdOut'] = stdout
      resultDict['StdErr'] = error
      resultDict['OwnerDN'] = owner
      resultDict['OwnerGroup'] = group
      resultDict['FileList'] = fileList
      return S_OK(resultDict)
    else:
      # Instantiate the appropriate CE
      ceFactory = ComputingElementFactory()
      result = getQueue( pilotDict['GridSite'], pilotDict['DestinationSite'], pilotDict['Queue'] )
      if not result['OK']:
        return result
      queueDict = result['Value']
      gridEnv = getGridEnv()
      queueDict['GridEnv'] = gridEnv
      queueDict['WorkingDirectory'] = mkdtemp()
      result = ceFactory.getCE( gridType, pilotDict['DestinationSite'], queueDict )
      if not result['OK']:
        shutil.rmtree( queueDict['WorkingDirectory'] )
        return result
      ce = result['Value']
      groupVOMS = getGroupOption(group,'VOMSRole',group)
      result = gProxyManager.getPilotProxyFromVOMSGroup( owner, groupVOMS )
      if not result['OK']:
        gLogger.error( result['Message'] )
        gLogger.error( 'Could not get proxy:', 'User "%s", Group "%s"' % ( owner, groupVOMS ) )
        return S_ERROR("Failed to get the pilot's owner proxy")
      proxy = result['Value']
      ce.setProxy( proxy )
      pilotStamp = pilotDict['PilotStamp']
      pRef = pilotReference
      if pilotStamp:
        pRef = pRef + ':::' + pilotStamp
      result = ce.getJobOutput( pRef )
      if not result['OK']:
        shutil.rmtree( queueDict['WorkingDirectory'] )
        return result
      stdout,error = result['Value']
      if stdout:
        result = pilotDB.storePilotOutput(pilotReference,stdout,error)
        if not result['OK']:
          gLogger.error('Failed to store pilot output:',result['Message'])

      resultDict = {}
      resultDict['StdOut'] = stdout
      resultDict['StdErr'] = error
      resultDict['OwnerDN'] = owner
      resultDict['OwnerGroup'] = group
      resultDict['FileList'] = []
      shutil.rmtree( queueDict['WorkingDirectory'] )
      return S_OK( resultDict )

  ##############################################################################
  types_getPilotSummary = []
  @staticmethod
  def export_getPilotSummary(startdate='', enddate=''):
    """ Get summary of the status of the LCG Pilot Jobs
    """

    result = pilotDB.getPilotSummary(startdate,enddate)
    return result

  ##############################################################################
  types_getPilotMonitorWeb = [DictType, ListType, IntType, IntType]
  @staticmethod
  def export_getPilotMonitorWeb(selectDict, sortList, startItem, maxItems):
    """ Get the summary of the pilot information for a given page in the
        pilot monitor in a generic format
    """

    result = pilotDB.getPilotMonitorWeb(selectDict, sortList, startItem, maxItems)
    return result

  ##############################################################################
  types_getPilotMonitorSelectors = []
  @staticmethod
  def export_getPilotMonitorSelectors():
    """ Get all the distinct selector values for the Pilot Monitor web portal page
    """

    result = pilotDB.getPilotMonitorSelectors()
    return result

  ##############################################################################
  types_getPilotSummaryWeb = [DictType, ListType, IntType, IntType]
  @staticmethod
  def export_getPilotSummaryWeb(selectDict, sortList, startItem, maxItems):
    """ Get the summary of the pilot information for a given page in the
        pilot monitor in a generic format
    """

    result = pilotDB.getPilotSummaryWeb(selectDict, sortList, startItem, maxItems)
    return result

  ##############################################################################
  types_getSiteSummaryWeb = [DictType, ListType, IntType, IntType]
  @staticmethod
  def export_getSiteSummaryWeb(selectDict, sortList, startItem, maxItems):
    """ Get the summary of the jobs running on sites in a generic format
    """

    result = jobDB.getSiteSummaryWeb(selectDict, sortList, startItem, maxItems)
    return result

  ##############################################################################
  types_getSiteSummarySelectors = []
  @staticmethod
  def export_getSiteSummarySelectors():
    """ Get all the distinct selector values for the site summary web portal page
    """

    resultDict = {}
    statusList = ['Good','Fair','Poor','Bad','Idle']
    resultDict['Status'] = statusList
    maskStatus = ['Active','Banned','NoMask','Reduced']
    resultDict['MaskStatus'] = maskStatus

    gridTypes = []
    result = gConfig.getSections('Resources/Sites/',[])
    if result['OK']:
      gridTypes = result['Value']

    resultDict['GridType'] = gridTypes
    siteList = []
    for grid in gridTypes:
      result = gConfig.getSections('Resources/Sites/%s' % grid,[])
      if result['OK']:
        siteList += result['Value']

    countryList = []
    for site in siteList:
      if site.find('.') != -1:
        country = site.split('.')[2].lower()
        if country not in countryList:
          countryList.append(country)
    countryList.sort()
    resultDict['Country'] = countryList
    siteList.sort()
    resultDict['Site'] = siteList

    return S_OK(resultDict)

  ##############################################################################
  types_getPilots = [list( StringTypes ) + [ IntType, LongType]]
  @staticmethod
  def export_getPilots(jobID):
    """ Get pilot references and their states for :
      - those pilots submitted for the TQ where job is sitting
      - (or) the pilots executing/having executed the Job
    """

    pilots = []
    result = pilotDB.getPilotsForJobID( int( jobID ) )
    if not result['OK']:
      if result['Message'].find('not found') == -1:
        return S_ERROR('Failed to get pilot: '+result['Message'])
    else:
      pilots += result['Value']
    if not pilots:
      # Pilots were not found try to look in the Task Queue
      taskQueueID = 0
      result = taskQueueDB.getTaskQueueForJob( int( jobID ) )
      if result['OK'] and result['Value']:
        taskQueueID = result['Value']
      if taskQueueID:
        result = pilotDB.getPilotsForTaskQueue( taskQueueID, limit=10 )
        if not result['OK']:
          return S_ERROR('Failed to get pilot: '+result['Message'])
        pilots += result['Value']

    if not pilots:
      return S_ERROR( 'Failed to get pilot for Job %d' % int( jobID ) )

    return pilotDB.getPilotInfo(pilotID=pilots)

  ##############################################################################
  types_killPilot = [ list(StringTypes)+[ListType] ]
  @staticmethod
  def export_killPilot(pilotRefList ):
    """ Kill the specified pilots
    """
    # Make a list if it is not yet
    pilotRefs = list( pilotRefList )
    if type( pilotRefList ) in StringTypes:
      pilotRefs = [pilotRefList]

    # Regroup pilots per site and per owner
    pilotRefDict = {}
    for pilotReference in pilotRefs:
      result = pilotDB.getPilotInfo(pilotReference)
      if not result['OK'] or not result[ 'Value' ]:
        return S_ERROR('Failed to get info for pilot ' + pilotReference)

      pilotDict = result['Value'][pilotReference]
      owner = pilotDict['OwnerDN']
      group = pilotDict['OwnerGroup']
      queue = '@@@'.join( [owner, group, pilotDict['GridSite'], pilotDict['DestinationSite'], pilotDict['Queue']] )
      gridType = pilotDict['GridType']
      pilotRefDict.setdefault( queue, {} )
      pilotRefDict[queue].setdefault( 'PilotList', [] )
      pilotRefDict[queue]['PilotList'].append( pilotReference )
      pilotRefDict[queue]['GridType'] = gridType

    # Do the work now queue by queue
    ceFactory = ComputingElementFactory()
    failed = []
    for key, pilotDict in pilotRefDict.items():

      owner,group,site,ce,queue = key.split( '@@@' )
      result = getQueue( site, ce, queue )
      if not result['OK']:
        return result
      queueDict = result['Value']
      gridType = pilotDict['GridType']
      result = ceFactory.getCE( gridType, ce, queueDict )
      if not result['OK']:
        return result
      ce = result['Value']

      # FIXME: quite hacky. Should be either removed, or based on some flag
      if gridType in ["LCG", "gLite", "CREAM", "ARC", "Globus"]:
        group = getGroupOption(group,'VOMSRole',group)
        ret = gProxyManager.getPilotProxyFromVOMSGroup( owner, group )
        if not ret['OK']:
          gLogger.error( ret['Message'] )
          gLogger.error( 'Could not get proxy:', 'User "%s", Group "%s"' % ( owner, group ) )
          return S_ERROR("Failed to get the pilot's owner proxy")
        proxy = ret['Value']
        ce.setProxy( proxy )

      pilotList = pilotDict['PilotList']
      result = ce.killJob( pilotList )
      if not result['OK']:
        failed.extend( pilotList )

    if failed:
      return S_ERROR('Failed to kill at least some pilots')

    return S_OK()

  ##############################################################################
  types_setJobForPilot = [ list( StringTypes ) + [ IntType, LongType], StringTypes]
  @staticmethod
  def export_setJobForPilot(jobID, pilotRef, destination=None):
    """ Report the DIRAC job ID which is executed by the given pilot job
    """

    result = pilotDB.setJobForPilot( int( jobID ), pilotRef )
    if not result['OK']:
      return result
    result = pilotDB.setCurrentJobID( pilotRef, int( jobID ) )
    if not result['OK']:
      return result
    if destination:
      result = pilotDB.setPilotDestinationSite(pilotRef,destination)

    return result

  ##########################################################################################
  types_setPilotBenchmark = [StringTypes, FloatType]
  @staticmethod
  def export_setPilotBenchmark(pilotRef, mark):
    """ Set the pilot agent benchmark
    """
    result = pilotDB.setPilotBenchmark(pilotRef,mark)
    return result

  ##########################################################################################
  types_setAccountingFlag = [StringTypes]
  @staticmethod
  def export_setAccountingFlag(pilotRef, mark='True'):
    """ Set the pilot AccountingSent flag
    """
    result = pilotDB.setAccountingFlag(pilotRef,mark)
    return result

  ##########################################################################################
  types_setPilotStatus = [StringTypes, StringTypes]
  def export_setPilotStatus(self,pilotRef,status,destination=None,reason=None,gridSite=None,queue=None):
    """ Set the pilot agent status
    """

    result = pilotDB.setPilotStatus(pilotRef,status,destination=destination,
                                    statusReason=reason,gridSite=gridSite,queue=queue)
    return result

  ##########################################################################################
  types_countPilots = [ DictType ]
  @staticmethod
  def export_countPilots(condDict, older=None, newer=None, timeStamp='SubmissionTime'):
    """ Set the pilot agent status
    """

    result = pilotDB.countPilots(condDict, older, newer, timeStamp )
    return result

  ##########################################################################################
  types_getCounters = [ StringTypes, ListType, DictType ]
  @staticmethod
  def export_getCounters(table, keys, condDict, newer=None, timeStamp='SubmissionTime'):
    """ Set the pilot agent status
    """

    result = pilotDB.getCounters( table, keys, condDict, newer=newer, timeStamp=timeStamp )
    return result

  ##############################################################################
  types_getPilotStatistics = [ StringTypes, DictType ]
  @staticmethod
  def export_getPilotStatistics ( attribute, selectDict ):
    """ Get pilot statistics distribution per attribute value with a given selection
    """

    startDate = selectDict.get( 'FromDate', None )
    if startDate:
      del selectDict['FromDate']

    if startDate is None:
      startDate = selectDict.get( 'LastUpdate', None )
      if startDate:
        del selectDict['LastUpdate']
    endDate = selectDict.get( 'ToDate', None )
    if endDate:
      del selectDict['ToDate']


    result = pilotDB.getCounters( 'PilotAgents', [attribute], selectDict,
                                  newer = startDate,
                                  older = endDate,
                                  timeStamp = 'LastUpdateTime' )
    statistics = {}
    if result['OK']:
      for status, count in result['Value']:
        if "OwnerDN" in status:
          userName = getUsernameForDN( status['OwnerDN'] )
          if userName['OK']:
            status['OwnerDN'] = userName['Value']
          statistics[ status['OwnerDN'] ] = count
        else:
          statistics[ status[attribute] ] = count

    return S_OK( statistics )
