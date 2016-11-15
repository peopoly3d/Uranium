# Copyright (c) 2016 Ultimaker B.V.
# Uranium is released under the terms of the AGPLv3 or higher.
import configparser
import io
from typing import Set, List, Optional, cast

from UM.Signal import Signal, signalemitter
from UM.PluginObject import PluginObject
from UM.Logger import Logger
from UM.MimeTypeDatabase import MimeTypeDatabase, MimeType
from UM.Settings.DefinitionContainer import DefinitionContainer #For getting all definitions in this stack.

import UM.Settings.ContainerRegistry

from UM.Settings.ContainerInterface import ContainerInterface
from UM.Settings.SettingFunction import SettingFunction

class IncorrectVersionError(Exception):
    pass


class InvalidContainerStackError(Exception):
    pass

MimeTypeDatabase.addMimeType(
    MimeType(
        name = "application/x-uranium-containerstack",
        comment = "Uranium Container Stack",
        suffixes = [ "stack.cfg" ]
    )
)

##  A stack of setting containers to handle setting value retrieval.
@signalemitter
class ContainerStack(ContainerInterface, PluginObject):
    Version = 2

    ##  Constructor
    #
    #   \param stack_id \type{string} A unique, machine readable/writable ID.
    def __init__(self, stack_id, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._id = str(stack_id)
        self._name = stack_id
        self._metadata = {}
        self._containers = []
        self._next_stack = None
        self._read_only = False
        self._dirty = True
        self._path = ""
        self._postponed_emits = []  # gets filled with 2-tuples: signal, signal_argument(s)

    ##  \copydoc ContainerInterface::getId
    #
    #   Reimplemented from ContainerInterface
    def getId(self) -> str:
        return self._id

    ##  \copydoc ContainerInterface::getName
    #
    #   Reimplemented from ContainerInterface
    def getName(self) -> str:
        return str(self._name)

    ##  Emitted whenever the name of this stack changes.
    nameChanged = Signal()

    containersChanged = Signal()

    ##  Set the name of this stack.
    #
    #   \param name \type{string} The new name of the stack.
    def setName(self, name: str) -> None:
        if name != self._name:
            self._name = name
            self.nameChanged.emit()

    ##  \copydoc ContainerInterface::isReadOnly
    #
    #   Reimplemented from ContainerInterface
    def isReadOnly(self):
        return self._read_only

    def setReadOnly(self, read_only):
        self._read_only = read_only

    ##  \copydoc ContainerInterface::getMetaData
    #
    #   Reimplemented from ContainerInterface
    def getMetaData(self):
        return self._metadata

    ##  \copydoc ContainerInterface::getMetaDataEntry
    #
    #   Reimplemented from ContainerInterface
    def getMetaDataEntry(self, entry, default = None):
        value = self._metadata.get(entry, None)

        if value is None:
            for container in self._containers:
                value = container.getMetaDataEntry(entry, None)
                if value is not None:
                    break

        if value is None:
            return default
        else:
            return value

    def addMetaDataEntry(self, key, value):
        if key not in self._metadata:
            self._dirty = True
            self._metadata[key] = value
        else:
            Logger.log("w", "Meta data with key %s was already added.", key)

    def setMetaDataEntry(self, key, value):
        if key in self._metadata:
            self._dirty = True
            self._metadata[key] = value
        else:
            Logger.log("w", "Meta data with key %s was not found. Unable to change.", key)

    def removeMetaDataEntry(self, key):
        if key in self._metadata:
            del self._metadata[key]

    def isDirty(self) -> bool:
        return self._dirty

    def setDirty(self, dirty: bool) -> None:
        self._dirty = dirty

    ##  \copydoc ContainerInterface::getProperty
    #
    #   Reimplemented from ContainerInterface.
    #
    #   getProperty will start at the top of the stack and try to get the property
    #   specified. If that container returns no value, the next container on the
    #   stack will be tried and so on until the bottom of the stack is reached.
    #   If a next stack is defined for this stack it will then try to get the
    #   value from that stack. If no next stack is defined, None will be returned.
    #
    #   Note that if the property value is a function, this method will return the
    #   result of evaluating that property with the current stack. If you need the
    #   actual function, use getRawProperty()
    def getProperty(self, key: str, property_name: str):
        value = self.getRawProperty(key, property_name)
        if isinstance(value, SettingFunction):
            return value(self)

        return value

    ##  Retrieve a property of a setting by key and property name.
    #
    #   This method does the same as getProperty() except it does not perform any
    #   special handling of the result, instead the raw stored value is returned.
    #
    #   \param key The key to get the property value of.
    #   \param property_name The name of the property to get the value of.
    #   \param use_next True if the value should be retrieved from the next
    #   stack if not found in this stack. False if not.
    #   \param skip_until_container A container ID to skip to. If set, it will
    #   be as if all containers above the specified container are empty. If the
    #   container is not in the stack, it'll try to find it in the next stack.
    #
    #   \return The raw property value of the property, or None if not found. Note that
    #           the value might be a SettingFunction instance.
    #
    def getRawProperty(self, key, property_name, *, use_next = True, skip_until_container = None):
        for container in self._containers:
            if skip_until_container and container.getId() != skip_until_container:
                continue #Skip.
            skip_until_container = None #When we find the container, stop skipping.

            value = container.getProperty(key, property_name)
            if value is not None:
                return value

        if self._next_stack and use_next:
            return self._next_stack.getRawProperty(key, property_name, use_next = use_next, skip_until_container = skip_until_container)
        else:
            return None

    ##  \copydoc ContainerInterface::hasProperty
    #
    #   Reimplemented from ContainerInterface.
    #
    #   hasProperty will check if any of the containers in the stack has the
    #   specified property. If it does, it stops and returns True. If it gets to
    #   the end of the stack, it returns False.
    def hasProperty(self, key: str, property_name: str) -> bool:
        for container in self._containers:
            if container.hasProperty(key, property_name):
                return True

        if self._next_stack:
            return self._next_stack.hasProperty(key, property_name)
        return False

    propertyChanged = Signal()

    ##  \copydoc ContainerInterface::serialize
    #
    #   Reimplemented from ContainerInterface
    #
    #   TODO: Expand documentation here, include the fact that this should _not_ include all containers
    def serialize(self):
        parser = configparser.ConfigParser(interpolation = None, empty_lines_in_values = False)

        parser["general"] = {}
        parser["general"]["version"] = str(self.Version)
        parser["general"]["name"] = str(self._name)
        parser["general"]["id"] = str(self._id)

        parser["metadata"] = {}
        for key, value in self._metadata.items():
            parser["metadata"][key] = str(value)

        container_id_string = ""
        for container in self._containers:
            container_id_string += str(container.getId()) + ","

        parser["general"]["containers"] = container_id_string

        stream = io.StringIO()
        parser.write(stream)
        return stream.getvalue()

    ##  \copydoc ContainerInterface::deserialize
    #
    #   Reimplemented from ContainerInterface
    #
    #   TODO: Expand documentation here, include the fact that this should _not_ include all containers
    def deserialize(self, serialized):
        parser = configparser.ConfigParser(interpolation=None, empty_lines_in_values=False)
        parser.read_string(serialized)

        if not "general" in parser or not "version" in parser["general"] or not "name" in parser["general"] or not "id" in parser["general"]:
            raise InvalidContainerStackError("Missing required section 'general' or 'version' property")

        if parser["general"].getint("version") != self.Version:
            raise IncorrectVersionError

        self._name = parser["general"].get("name")
        self._id = parser["general"].get("id")

        if "metadata" in parser:
            self._metadata = dict(parser["metadata"])

        # The containers are saved in a single comma-separated list.
        container_string = parser["general"].get("containers", "")
        Logger.log("d", "While deserializing, we got the following container string: %s", container_string)
        container_id_list = container_string.split(",")
        for container_id in container_id_list:
            if container_id != "":
                containers = UM.Settings.ContainerRegistry.getInstance().findContainers(id = container_id)
                if containers:
                    containers[0].propertyChanged.connect(self.propertyChanged)
                    self._containers.append(containers[0])
                else:
                    raise Exception("When trying to deserialize %s, we received an unknown ID (%s) for container" % (self._id, container_id))

        ## TODO; Deserialize the containers.

    ##  Get all keys known to this container stack.
    #
    #   In combination with getProperty(), you can obtain the current property
    #   values of all settings.
    #
    #   \return A set of all setting keys in this container stack.
    def getAllKeys(self) -> Set[str]:
        keys = set()    # type: Set[str]
        definition_containers = [container for container in self.getContainers() if container.__class__ == DefinitionContainer] #To get all keys, get all definitions from all definition containers.
        for definition_container in cast(List[DefinitionContainer], definition_containers):
            keys |= definition_container.getAllKeys()
        if self._next_stack:
            keys |= self._next_stack.getAllKeys()
        return keys

    ##  Get a list of all containers in this stack.
    #
    #   Note that it returns a shallow copy of the container list, as it's only allowed to change the order or entries
    #   in this list by the proper functions.
    #   \return \type{list} A list of all containers in this stack.
    def getContainers(self) -> List[ContainerInterface]:
        return self._containers[:]

    def getContainerIndex(self, container: ContainerInterface) -> int:
        return self._containers.index(container)

    ##  Get a container by index.
    #
    #   \param index \type{int} The index of the container to get.
    #
    #   \return The container at the specified index.
    #
    #   \exception IndexError Raised when the specified index is out of bounds.
    def getContainer(self, index: int) -> ContainerInterface:
        if index < 0:
            raise IndexError
        return self._containers[index]

    ##  Get the container at the top of the stack.
    #
    #   This is a convenience method that will always return the top of the stack.
    #
    #   \return The container at the top of the stack, or None if no containers have been added.
    def getTop(self) -> ContainerInterface:
        if self._containers:
            return self._containers[0]

        return None

    ##  Get the container at the bottom of the stack.
    #
    #   This is a convenience method that will always return the bottom of the stack.
    #
    #   \return The container at the bottom of the stack, or None if no containers have been added.
    def getBottom(self) -> ContainerInterface:
        if self._containers:
            return self._containers[-1]

        return None

    ##  \copydoc ContainerInterface::getPath.
    #
    #   Reimplemented from ContainerInterface
    def getPath(self):
        return self._path

    ##  \copydoc ContainerInterface::setPath
    #
    #   Reimplemented from ContainerInterface
    def setPath(self, path):
        self._path = path

    ##  Get the SettingDefinition object for a specified key
    def getSettingDefinition(self, key: str):
        for container in self._containers:
            if not isinstance(container, DefinitionContainer):
                continue

            settings = container.findDefinitions(key = key)
            if settings:
                return settings[0]

        if self._next_stack:
            return self._next_stack.getSettingDefinition(key)
        else:
            return None

    ##  Find a container matching certain criteria.
    #
    #   \param filter \type{dict} A dictionary containing key and value pairs
    #   that need to match the container. Note that the value of "*" can be used
    #   as a wild card. This will ensure that any container that has the
    #   specified key in the meta data is found.
    #   \param container_type \type{class} An optional type of container to
    #   filter on.
    #   \return The first container that matches the filter criteria or None if not found.
    def findContainer(self, criteria = None, container_type = None, **kwargs) -> Optional[ContainerInterface]:
        if not criteria and kwargs:
            criteria = kwargs

        for container in self._containers:
            meta_data = container.getMetaData()
            match = True
            for key in criteria:
                try:
                    if (meta_data[key] == criteria[key] or criteria[key] == "*") and (container.__class__ == container_type or container_type == None):
                        continue
                    else:
                        match = False
                        break
                except KeyError:
                    match = False
                    break

            if match:
                return container

        return None

    ##  Add a container to the top of the stack.
    #
    #   \param container The container to add to the stack.
    def addContainer(self, container):
        if container is not self:
            container.propertyChanged.connect(self.propertyChanged)
            self._containers.insert(0, container)
            self.containersChanged.emit(container)
        else:
            raise Exception("Unable to add stack to itself.")

    ##  Replace a container in the stack.
    #
    #   \param index \type{int} The index of the container to replace.
    #   \param container The container to replace the existing entry with.
    #   \param postpone_emit  During stack manipulation you may want to emit later.
    #
    #   \exception IndexError Raised when the specified index is out of bounds.
    #   \exception Exception when trying to replace container ContainerStack.
    def replaceContainer(self, index, container, postpone_emit=False):
        if index < 0:
            raise IndexError
        if container is self:
            raise Exception("Unable to replace container with ContainerStack (self) ")

        self._containers[index].propertyChanged.disconnect(self.propertyChanged)
        container.propertyChanged.connect(self.propertyChanged)
        self._containers[index] = container
        if postpone_emit:
            # send it using sendPostponedEmits
            self._postponed_emits.append((self.containersChanged, container))
        else:
            self.containersChanged.emit(container)

    ##  Remove a container from the stack.
    #
    #   \param index \type{int} The index of the container to remove.
    #
    #   \exception IndexError Raised when the specified index is out of bounds.
    def removeContainer(self, index = 0):
        if index < 0:
            raise IndexError
        try:
            container = self._containers[index]
            container.propertyChanged.disconnect(self.propertyChanged)
            del self._containers[index]
            self.containersChanged.emit(container)
        except TypeError:
            raise IndexError("Can't delete container with index %s" % index)

    ##  Get the next stack
    #
    #   The next stack is the stack that is searched for a setting value if the
    #   bottom of the stack is reached when searching for a value.
    #
    #   \return \type{ContainerStack} The next stack or None if not set.
    def getNextStack(self):
        return self._next_stack

    ##  Set the next stack
    #
    #   \param stack \type{ContainerStack} The next stack to set. Can be None.
    #   Raises Exception when trying to set itself as next stack (to prevent infinite loops)
    #   \sa getNextStack
    def setNextStack(self, stack):
        if self is stack:
            raise Exception("Next stack can not be itself")
        if self._next_stack == stack:
            return
        # Link the propertyChanged signal of next to self.
        if self._next_stack:
            self._next_stack.propertyChanged.disconnect(self.propertyChanged)
        self._next_stack = stack
        if self._next_stack:
            self._next_stack.propertyChanged.connect(self.propertyChanged)

    ##  Send postponed emits
    #   These emits are collected from the option postpone_emit.
    #   Note: the option can be implemented for all functions modifying the stack.
    def sendPostponedEmits(self):
        while self._postponed_emits:
            signal, signal_arg = self._postponed_emits.pop(0)
            signal.emit(signal_arg)

    ##  Check if the container stack has errors
    def hasErrors(self):
        for key in self.getAllKeys():
            validation_state = self.getProperty(key, "validationState")
            if validation_state in (UM.Settings.ValidatorState.Exception, UM.Settings.ValidatorState.MaximumError,
            UM.Settings.ValidatorState.MinimumError):
                return True
        return False

    ##  Get all the keys that are in an error state in this stack
    def getErrorKeys(self):
        error_keys = []
        for key in self.getAllKeys():
            validation_state = self.getProperty(key, "validationState")
            if validation_state in (UM.Settings.ValidatorState.Exception, UM.Settings.ValidatorState.MaximumError,
                                    UM.Settings.ValidatorState.MinimumError):
                error_keys.append(key)
        return error_keys
