#! /usr/bin/python3.7
# -- coding: utf-8 -- **
"""Electrodes tab layout -- standard Qt theme (no custom stylesheets/backgrounds),
per the same redesign rules applied to new_patient_dialog.py. The old "Import Data"
groupbox (CT/surf browse buttons) is gone entirely -- the CT and reconstruction are
already server-side by the time this tab is used (uploaded via the New Patient
dialog), so client_elec.py just checks readiness and disables the whole page with an
explanatory status_label if the data isn't there yet, instead of offering an import
step.
"""

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QApplication, QMainWindow, QSizePolicy, QMessageBox, QWidget, \
    QPushButton, QLineEdit, QDesktopWidget, QGridLayout, QFileDialog, QListWidget, QLabel, \
    QFrame, QGroupBox, QTableWidget
from PyQt5.QtCore import Qt, QThread


class Electrodes_gui(object):

    def setupUi(self, Electrodes):
        Electrodes.setObjectName("Electrodes")
        self.setWindowTitle('Electrode Module')
        self.gridlayout = QGridLayout()
        self.gridlayout.setSpacing(10)
        self.gridlayout.setContentsMargins(15, 15, 15, 15)
        self.gridlayout.setColumnMinimumWidth(0, 800)
        Electrodes.setLayout(self.gridlayout)

        # status banner -- explains why the page is disabled (no subject, no
        # reconstruction, no CT), or that it's ready / a background step is running
        self.status_label = QtWidgets.QLabel(Electrodes)
        self.status_label.setWordWrap(True)
        self.gridlayout.addWidget(self.status_label, 0, 0, 1, 12)

        # the container everything below lives in -- toggled enabled/disabled as a
        # whole based on subject readiness (see client_elec.py's set_subject/
        # _apply_readiness)
        self.content = QtWidgets.QWidget(Electrodes)
        self.gridlayout.addWidget(self.content, 1, 0, 28, 12)
        content_layout = QGridLayout(self.content)
        content_layout.setSpacing(10)
        content_layout.setColumnMinimumWidth(0, 800)

        # display box
        self.graphicsView = QtWidgets.QGraphicsView(self.content)
        self.graphicsView.setObjectName("DisplayData")
        self.graphicsView.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        content_layout.addWidget(self.graphicsView, 0, 0, 28, 7)

        # preprocess groupbox
        self.groupBox_2 = QtWidgets.QGroupBox(self.content)
        self.groupBox_2.setObjectName("PreprocessData")
        self.groupBox_2.setTitle("Preprocess Data")
        self.groupBox_2.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        content_layout.addWidget(self.groupBox_2, 0, 8, 6, 4)

        self.pushButton_3 = QtWidgets.QPushButton(self.groupBox_2)
        self.pushButton_3.setObjectName("Preprocess")
        self.pushButton_3.setText("Preprocess")
        content_layout.addWidget(self.pushButton_3, 4, 9, 1, 1)

        self.pushButton_4 = QtWidgets.QPushButton(self.groupBox_2)
        self.pushButton_4.setObjectName("View")
        self.pushButton_4.setText("View Results")
        content_layout.addWidget(self.pushButton_4, 4, 10, 1, 1)

        self.label_1 = QtWidgets.QLabel(self.groupBox_2)
        self.label_1.setText("Number of Electrodes:")
        content_layout.addWidget(self.label_1, 1, 9, 1, 1)
        self.label_2 = QtWidgets.QLabel(self.groupBox_2)
        self.label_2.setText("Threshold:")
        content_layout.addWidget(self.label_2, 2, 9, 1, 1)
        self.label_3 = QtWidgets.QLabel(self.groupBox_2)
        self.label_3.setText("Erosion times:")
        content_layout.addWidget(self.label_3, 3, 9, 1, 1)

        self.lineEdit_3 = QtWidgets.QLineEdit(self.groupBox_2)
        self.lineEdit_3.setObjectName("NumberofElecs")
        content_layout.addWidget(self.lineEdit_3, 1, 10, 1, 1)

        self.lineEdit_4 = QtWidgets.QLineEdit(self.groupBox_2)
        self.lineEdit_4.setObjectName("NumberofErosions")
        content_layout.addWidget(self.lineEdit_4, 3, 10, 1, 1)

        self.doubleSpinBox_1 = QtWidgets.QDoubleSpinBox(self.groupBox_2)
        self.doubleSpinBox_1.setObjectName("threshold")
        self.doubleSpinBox_1.setFixedWidth(100)
        self.doubleSpinBox_1.setSuffix("%")
        self.doubleSpinBox_1.setMinimum(0)
        self.doubleSpinBox_1.setMaximum(100)
        self.doubleSpinBox_1.setSingleStep(1)
        self.doubleSpinBox_1.setDecimals(2)
        content_layout.addWidget(self.doubleSpinBox_1, 2, 10, 1, 1)

        self.pushButton_11 = QtWidgets.QPushButton(self.groupBox_2)
        self.pushButton_11.setObjectName("Optimize")
        self.pushButton_11.setText("Optimize Params")
        self.pushButton_11.setToolTip("search threshold & erosion times to match the given number of electrodes")
        content_layout.addWidget(self.pushButton_11, 5, 9, 1, 2)

        # process groupbox
        self.groupBox_3 = QtWidgets.QGroupBox(self.content)
        self.groupBox_3.setObjectName("ProcessData")
        self.groupBox_3.setTitle("Process Data")
        self.groupBox_3.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        content_layout.addWidget(self.groupBox_3, 7, 8, 16, 4)

        self.pushButton_5 = QtWidgets.QPushButton(self.groupBox_3)
        self.pushButton_5.setObjectName("Label")
        self.pushButton_5.setText("Label")
        content_layout.addWidget(self.pushButton_5, 8, 9, 1, 1)

        self.pushButton_6 = QtWidgets.QPushButton(self.groupBox_3)
        self.pushButton_6.setObjectName("ViewLabels")
        self.pushButton_6.setText("View Labels")
        content_layout.addWidget(self.pushButton_6, 8, 10, 1, 1)

        self.pushButton_7 = QtWidgets.QPushButton(self.groupBox_3)
        self.pushButton_7.setObjectName("Segment")
        self.pushButton_7.setText("Contact Segment")
        content_layout.addWidget(self.pushButton_7, 10, 9, 1, 1)

        self.pushButton_8 = QtWidgets.QPushButton(self.groupBox_3)
        self.pushButton_8.setObjectName("labelsDone")
        self.pushButton_8.setText("Done")
        content_layout.addWidget(self.pushButton_8, 9, 9, 1, 1)

        self.pushButton_9 = QtWidgets.QPushButton(self.groupBox_3)
        self.pushButton_9.setObjectName("contactView")
        self.pushButton_9.setText("View Contacts")
        content_layout.addWidget(self.pushButton_9, 9, 10, 1, 1)

        self.pushButton_10 = QtWidgets.QPushButton(self.groupBox_3)
        self.pushButton_10.setObjectName("contactDone")
        self.pushButton_10.setText("All set")
        content_layout.addWidget(self.pushButton_10, 10, 10, 1, 1)

        self.tableWidget = QtWidgets.QTableWidget(self.groupBox_3)
        self.tableWidget.setObjectName("CheckList")
        self.tableWidget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.tableWidget.setColumnCount(3)
        self.tableWidget.setColumnWidth(0, 100)
        self.tableWidget.setColumnWidth(1, 100)
        self.tableWidget.setColumnWidth(2, 305)
        self.tableWidget.setHorizontalHeaderLabels(['Label', '#Contact', 'Location'])
        self.tableWidget.setSelectionMode(QTableWidget.SingleSelection)
        self.tableWidget.setSelectionBehavior(QTableWidget.SelectRows)
        self.tableWidget.horizontalHeader().setSectionsClickable(False)
        self.tableWidget.verticalHeader().setVisible(False)
        self.tableWidget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tableWidget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content_layout.addWidget(self.tableWidget, 11, 9, 11, 2)

        self.pushButton_3.clicked.connect(Electrodes.preprocessData)
        self.pushButton_4.clicked.connect(Electrodes.viewIntra)
        self.pushButton_11.clicked.connect(Electrodes.optimizeParams)
        self.lineEdit_3.textEdited.connect(Electrodes.numberK)
        self.lineEdit_4.textEdited.connect(Electrodes.numberEro)
        self.doubleSpinBox_1.valueChanged.connect(Electrodes.threSel)
        self.pushButton_5.clicked.connect(Electrodes.labelGen)
        self.pushButton_6.clicked.connect(Electrodes.viewLabels)
        self.pushButton_7.clicked.connect(Electrodes.contactSeg)
        self.pushButton_8.clicked.connect(Electrodes.labelsDone)
        self.pushButton_9.clicked.connect(Electrodes.viewContacts)
        self.pushButton_10.clicked.connect(Electrodes.allSet)
        self.tableWidget.itemDoubleClicked.connect(Electrodes.elecAdjust)
        QtCore.QMetaObject.connectSlotsByName(Electrodes)

        self.lineEdit_3.setEnabled(False)
        self.lineEdit_4.setEnabled(False)
        self.pushButton_3.setEnabled(False)
        self.pushButton_4.setEnabled(False)
        self.pushButton_11.setEnabled(False)
        self.pushButton_5.setEnabled(False)
        self.pushButton_6.setEnabled(False)
        self.pushButton_7.setEnabled(False)
        self.pushButton_8.setEnabled(False)
        self.pushButton_9.setEnabled(False)
        self.pushButton_10.setEnabled(False)

        # the whole content area starts disabled -- client_elec.py's set_subject /
        # readiness check re-enables it once a subject with a finished reconstruction
        # (and CT) is selected
        self.content.setEnabled(False)
