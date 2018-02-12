# Copyright (c) 2018 Ultimaker B.V.
# Uranium is released under the terms of the LGPLv3 or higher.

import os

from PyQt5.QtCore import Qt, QCoreApplication, pyqtSlot, pyqtProperty, pyqtSignal

from UM.Application import Application
from UM.Qt.ListModel import ListModel
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.Resources import Resources

class PluginsModel(ListModel):
    def __init__(self, parent = None):
        super().__init__(parent)

        self._view = "installed"

        self._registry = Application.getInstance().getPluginRegistry()
        self._required_plugins = Application.getInstance().getRequiredPlugins()

        self.addRoleName(Qt.UserRole + 1, "view")
        # Static props:
        # These should be defined in plugin.json and are read-only.
        self.addRoleName(Qt.UserRole + 2, "id")
        self.addRoleName(Qt.UserRole + 3, "name")
        self.addRoleName(Qt.UserRole + 4, "version")
        self.addRoleName(Qt.UserRole + 5, "author")
        self.addRoleName(Qt.UserRole + 6, "author_email")
        self.addRoleName(Qt.UserRole + 7, "description")

        # Computed props:
        # These are computed based on the user's system and interactions.
        self.addRoleName(Qt.UserRole + 8, "external")
        self.addRoleName(Qt.UserRole + 9, "file_location")
        self.addRoleName(Qt.UserRole + 10, "status")
        self.addRoleName(Qt.UserRole + 11, "enabled")
        self.addRoleName(Qt.UserRole + 12, "required")
        self.addRoleName(Qt.UserRole + 13, "can_uninstall")
        self.addRoleName(Qt.UserRole + 14, "can_upgrade")
        self.addRoleName(Qt.UserRole + 15, "update_url")

        self._update();

    def setView(self, view):
        if self._view != view:
            self._view = view
            self.viewChanged.emit()
            self._update()

    viewChanged = pyqtSignal()
    @pyqtProperty(str, fset = setView, notify = viewChanged)
    def view(self):
        return self._view

    def _update(self):

        if self._view == "available":
            self._plugins = self._registry.getExternalPlugins()
            Logger.log("i", "Switching to available plugins.")

        else:
            self._plugins = self._registry.getInstalledPlugins()
            Logger.log("i", "Switching to installed plugins.")

        items = []

        # Get all active plugins from registry (list of strings):
        active_plugins = self._registry.getActivePlugins()
        installed_plugins = self._registry.getInstalledPlugins()
        plugin_folder = os.path.join(Resources.getStoragePath(Resources.Resources), "plugins")

        # Metadata is used as the official list of "all plugins":
        for plugin_id in self._plugins:
            print(plugin_id)
            metadata = self._registry.getMetaData(plugin_id)

            if "plugin" not in metadata:
                Logger.log("e", "%s is missing a plugin metadata entry", plugin_id)
                continue

            props = metadata["plugin"]

            items.append({
                # Static props from above are taken from the plugin's metadata:
                "id": metadata["id"],
                "name": props.get("name", props.get("label", metadata["id"])),
                "version": props.get("version", "Unknown"),
                "author": props.get("author", "Anonymous"),
                "author_email": props.get("author_email", "plugins@ultimaker.com"),
                "description": props.get("description", props.get("short_description", "No description provided...")),

                # Computed props from above are computed
                "external": True if metadata["id"] in self._registry._plugins_external else False,
                "file_location": props.get("file_location", "/"),
                "status": "installed" if metadata["id"] in installed_plugins else "available",
                "enabled": True if self._view == "available" else metadata["id"] in active_plugins,
                "required": metadata["id"] in self._required_plugins,
                "can_uninstall": True if self._registry._locatePlugin(plugin_id, plugin_folder) else False,
                "can_upgrade": False, # Default, potentially overwritten by plugin browser
                "update_url": None # Default, potentially overwritten by plugin browser
            })

        items.sort(key = lambda k: k["name"])
        self.setItems(items)
