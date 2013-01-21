import os, sys
from PyQt4.QtCore import *
from PyQt4.QtGui import *
from Orange.widgets import widget, gui, settings
from Orange.widgets.settings import Setting
from Orange.data.table import Table
from Orange.data import io
from Orange.data import StringVariable, DiscreteVariable, ContinuousVariable


NAME = "File"
ID = "orange.widgets.data.file"

DESCRIPTION = """
Read a data table from a supported file format on the the file system and
send it to the the output.
"""

LONG_DESCRIPTION = """
This is the widget you will probably use at the start of every schema to read
the input data file (data table with examples). The widget maintains a
history of most recently used data files. For convenience, the history
also includes a directory with the sample data sets that come with Orange.
"""

ICON = "icons/File.svg"
AUTHOR = "Janez Demsar"
MAINTAINER_EMAIL = "janez.demsar(@at@)fri.uni-lj.si"
PRIORITY = 10
CATEGORY = "Data"

KEYWORDS = ["data", "file", "load", "read"]

OUTPUTS = [{"name": "Data",
            "type": Table,
            "doc": "Attribute-valued data set read from the input file."}]

WIDGET_CLASS = "OWFile"



class FileNameContextHandler(settings.ContextHandler):
    def match(self, context, _, filename):
        return context.filename == filename


# TODO What is this?!
def addOrigin(examples, filename):
    vars = examples.domain.variables + examples.domain.metas
    strings = [var for var in vars if isinstance(var, StringVariable)]
    dirname, basename = os.path.split(filename)
    for var in strings:
        if hasattr(var, "type") and not hasattr(var, "origin"):
            var.attributes["origin"] = dirname
    s = []
    s.append()

class OWFile(widget.OWWidget):
    """Reads data from a file"""
    recent_files = Setting(lambda: ["(none)"])
    new_variables = Setting(False, Setting.CONTEXT)

#    registeredFileTypes = [ft for ft in orange.getRegisteredFileTypes() if len(ft)>2 and ft[2]]
    registered_file_types = []
    dlgFormats = (
        "Tab-delimited files (*.tab *.txt)\nBasket files (*.basket)\n" +
        "".join("{0} ({1})\n".format(*ft) for ft in registered_file_types) +
        "All files(*.*)")
    formats = {".tab": "Tab-delimited file", ".txt": "Tab-delimited file",
               ".basket": "Basket file"}
    formats.update(dict((ft[1][2:], ft[0]) for ft in registered_file_types))


    def __init__(self, parent=None, signalManager = None):
        super().__init__(parent, signalManager, "File",
                         wantMainArea=0, resizingEnabled=1)

        self.domain = None
        self.recent_files = [fn for fn in self.recent_files
                             if os.path.exists(fn)]
        self.loaded_file = ""

        vbox = gui.widgetBox(self.controlArea, "Data File", addSpace=True)
        box = gui.widgetBox(vbox, orientation=0)
        self.filecombo = QComboBox(box)
        self.filecombo.setMinimumWidth(300)
        box.layout().addWidget(self.filecombo)
        self.connect(self.filecombo, SIGNAL('activated(int)'), self.selectFile)

        button = gui.button(box, self, '...', callback=self.browseFile)
        button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        button = gui.button(box, self, "Reload",
                            callback=self.reload, default=True)
        button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        gui.checkBox(vbox, self, "new_variables",
                     "Treat variables from different files as different")

        box = gui.widgetBox(self.controlArea, "Info", addSpace=True)
        self.infoa = gui.widgetLabel(box, 'No data loaded.')
        self.infob = gui.widgetLabel(box, ' ')
        self.warnings = gui.widgetLabel(box, ' ')
        #Set word wrap so long warnings won't expand the widget
        self.warnings.setWordWrap(True)
        self.warnings.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.MinimumExpanding)

        gui.rubber(self.controlArea)

        self.set_file_list()
        if len(self.recent_files) > 0:
            self.open_file(self.recent_files[0])


    def set_file_list(self):
        self.filecombo.clear()
        if not self.recent_files:
            self.filecombo.addItem("(none)")
        for file in self.recent_files:
            if file == "(none)":
                self.filecombo.addItem("(none)")
            else:
                self.filecombo.addItem(os.path.split(file)[1])
        self.filecombo.addItem("Browse documentation data sets...")


    def reload(self):
        if self.recent_files:
            return self.open_file(self.recent_files[0])


    def selectFile(self, n):
        if n < len(self.recent_files) :
            name = self.recent_files[n]
            del self.recent_files[n]
            self.recent_files.insert(0, name)
        elif n:
            self.browseFile(True)

        if len(self.recent_files) > 0:
            self.set_file_list()
            self.open_file(self.recent_files[0])

    def browseFile(self, in_demos=0):
        if in_demos:
            try:
                import orngConfiguration
                startfile = orngConfiguration.datasetsPath
            except AttributeError:
                startfile = ""
            if not startfile or not os.path.exists(startfile):
                widgetsdir = os.path.dirname(gui.__file__)
                orangedir = os.path.dirname(widgetsdir)
                startfile = os.path.join(orangedir, "doc", "datasets")
            if not startfile or not os.path.exists(startfile):
                d = os.getcwd()
                if os.path.basename(d) == "canvas":
                    d = os.path.dirname(d)
                startfile = os.path.join(os.path.dirname(d), "doc", "datasets")
            if not os.path.exists(startfile):
                QMessageBox.information(None, "File",
                    "Cannot find the directory with example data sets")
                return
        else:
            if self.recent_files and self.recent_files[0] != "(none)":
                startfile = self.recent_files[0]
            else:
                startfile = os.path.expanduser("~/")

        filename = QFileDialog.getOpenFileName(
            self, 'Open Orange Data File', startfile, self.dlgFormats)
        if not filename:
            return
        if filename in self.recent_files:
            self.recent_files.remove(filename)
        self.recent_files.insert(0, filename)
        self.set_file_list()
        self.open_file(self.recent_files[0])


    # Open a file, create data from it and send it over the data channel
    def open_file(self, fn):
        self.error()
        self.warning()
        self.information()

        if not os.path.exists(fn):
            dirname, basename = os.path.split(fn)
            if os.path.exists(os.path.join(".", basename)):
                fn = os.path.join(".", basename)
                self.information("Loading '{}' from the current directory."
                                 .format(basename))
        if fn == "(none)":
            self.send("Data", None)
            self.infoa.setText("No data loaded")
            self.infob.setText("")
            self.warnings.setText("")
            return

        self.loaded_file = ""

        data = None
        try:
            if self.processingHandler:
                self.processingHandler(self, 1)    # focus on active widget
            # TODO handle self.new_variables
            data = Table(fn)
            self.loaded_file = fn
        except Exception as errValue:
            if "is being loaded as" in errValue:
                try:
                    data = Table(fn)
                    self.loaded_file = fn
                    self.warning(0, errValue)
                except:
                    self.error(errValue)
                    self.infoa.setText('Data was not loaded due to an error.')
                    self.infob.setText('Error:')
                    self.warnings.setText(errValue)
        finally:
            if self.processingHandler:
                self.processingHandler(self, 0)    # remove focus from this widget

        if data is None:
            self.dataReport = None
        else:
            domain = data.domain
            self.infoa.setText(
                '{} instance(s), {} feature(s), {} meta attributes'
                .format(len(data), len(domain.attributes), len(domain.metas)))
            if isinstance(domain.class_var, ContinuousVariable):
                self.infob.setText('Regression; Numerical class.')
            elif isinstance(domain.class_var, DiscreteVariable):
                self.infob.setText('Classification; Discrete class with {} values.'
                                   .format(len(domain.class_var.values)))
            elif data.domain.class_vars:
                self.infob.setText('Multi-target; {} target variables.'
                                   .format(len(data.domain.class_vars)))
            else:
                self.infob.setText("Data has no target variable.")

            addOrigin(data, fn)
            # make new data and send it
            fName = os.path.split(fn)[1]
            if "." in fName:
                data.name = fName[:fName.rfind('.')]
            else:
                data.name = fName

            self.dataReport = self.prepareDataReport(data)
        self.send("Data", data)

    def sendReport(self):
        dataReport = getattr(self, "dataReport", None)
        if dataReport:
            self.reportSettings("File",
                                [("File name", self.loaded_file),
                                 ("Format", self.formats.get(os.path.splitext(self.loaded_file)[1], "unknown format"))])
            self.reportData(self.dataReport)

if __name__ == "__main__":
    a = QApplication(sys.argv)
    ow = OWFile()
    ow.show()
    a.exec_()
    ow.saveSettings()
