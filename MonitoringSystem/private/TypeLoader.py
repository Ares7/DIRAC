"""
It is used to load the monitoring types.

"""
import re

from DIRAC.Core.Utilities import DIRACSingleton
from DIRAC.Core.Utilities.Plotting.ObjectLoader import loadObjects

from DIRAC.MonitoringSystem.Client.Types.BaseType import BaseType

__RCSID__ = "$Id$"

########################################################################
class TypeLoader( object ):

  """
  .. class:: BaseType

  :param ~DIRACSingleton.DIRACSingleton metaclass: this is a singleton
  :param dict loaded: it stores the loaded classes
  :param str path: The location of the classes
  :param ~DIRAC.MonitoringSystem.Client.Types.BaseType.BaseType parentCls: it is the parent class
  :param regexp: regular expression...
  """
  __metaclass__ = DIRACSingleton.DIRACSingleton
  __loaded = {}
  __path = ""
  __parentCls = None
  __reFilter = None

  ########################################################################
  def __init__( self ):
    """c'tor
    """
    self.__loaded = {}
    self.__path = "MonitoringSystem/Client/Types"
    self.__parentCls = BaseType
    self.__reFilter = re.compile( r".*[a-z1-9]\.py$" )

  ########################################################################
  def getTypes( self ):
    """
    It returns all monitoring classes
    """
    if not self.__loaded:
      self.__loaded = loadObjects( self.__path, self.__reFilter, self.__parentCls )
    return self.__loaded
