# ver0.21

import os
import sys
import time
import re
import PySide6
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QModelIndex, Signal
from PySide6.QtGui import QStandardItemModel, QStandardItem, QAction, QGuiApplication,QValidator
from PySide6.QtWidgets import QApplication,QErrorMessage,QItemDelegate,QMainWindow,QVBoxLayout,QWidget,QAbstractItemView,QHeaderView,QStyleFactory,QMessageBox

# 👌 已支持的语法列表
tokenSpecification = [
	("STARTFLAG", r"^( *?)//"),         # 起始符
	("HEADING", r"<h>"),                # h: 标题(创建分支节点)
	("CHECKHEADING", r"<e[.0-9]*>"),    # e: 可选标题(创建分支节点)
	("NUMOPT", r"<o([^<\n])*?>"),       # o: 带有范围的数字选项(创建叶节点) 	## 接受三种范围		1. 列表   2. <o.x> 修改指定位 
																				##					3. <o.x..y> xxxx <l-u:s> 同时指定修改位和修改范围
	("CODEENABLE", r"<!{0,1}c[0-9]*>"), # c: 注释复选框(创建分支节点)
	("NOTIFICATION", r"<n>"),           # n: 文本信息(创建叶节点)
	("HELPINFO", r"<i>"),               # i: 帮助信息(不占用节点，附加到前一个节点上)
	("STRING", r"<s>"),                 # s: 帮助信息(不占用节点，附加到前一个节点上)
	("FLAG", r"<q>"),                   # q: 标志位复选框(创建叶节点) 			## 实际效果等价于没有子节点的 e 
	("SYMBOL_NUMBER", r"<y>"),          # y: 符号或数字(创建叶节点)
	("DEFAULT", r"<d>"),                # d: 默认配置
	("ESCAPE", r"</[hec]>"),            # 退出节点
	("DEFINE", r"^(?!.*//) *#define"),  # 没有被 // 注释的任何 #define
	("LISTITEM", r"<((([0-9]{1,}\.{1}[0-9]{1,})|[0-9]*)|[\S]*?)=>"),	# l: 可选列表
	("RANGEMODIFIER", r"(<[0-9.]*(\.{2}|-)[0-9.]*:??[0-9.]*>)"),  		# r: 范围限定(不占用节点) 对前一个节点进行修饰
	("MODIFIER", r"<#[+\-\*/](([0-9]{1,}\.{1}[0-9]{1,})|[0-9]*)>"),  	# m: 对显示值修饰后得到实际值
	("REGIONSTART", r"<<< Use Configuration Wizard in Context Menu >>>"),
	("REGIONEND", r"<<< end of configuration section >>>")
]

styleSheet = ""
userFont = "0xProto Nerd Font"
SafeMode = 1	# 安全模式下会将原文件备份，否则直接删除
passinaFile = None
# passinaFile = r"//wsl.localhost/DevLinux/home/reglucis/project/YueShell/Sys/FileSystem/FatFs/fatfs_conf.h"
# WizardAnnotations 节点类
class ConfigurationNode:
	def __init__(self, identifier = None, lastNode = None):
		self.identifier = identifier
		self.lastNode = lastNode
		self.description = None
		self.helpInfo = []
		self.childNodeTree = []
		self.TreeViewItem = None
		self.skipItem = 0
		self.default = None

		# 匹配的 define
		self.bindingDefineName = None
		self.bindingDefineValue = None
		
		# 适用于 <?.x> mask 只允许设置一位，即 (1 << k)
		self.mask = 0

		# 定义：step 用于判断 bindingDefineValue 值类型
		# 定义：不指定 step 时, 默认为 int(1)
		# 定义：upperLimit 和 lowerLimit 必须成对出现
		self.upperLimit = None
		self.lowerLimit = None
		self.step = None

		# 复选框 c
		self.check = None
		self.startLine = None
		self.endLine = None

		# 下拉框
		self.comboListName = []
		self.comboListValue = []

	def addChild(self, node):
		self.childNodeTree.append(node)

	def addInfo(self, node):
		self.helpInfo.append(node)

	def describe(self, description: str):
		self.description = description

	def bindTreeViewItem(self, item):
		self.TreeViewItem = item

class ConfigurationListItem:
	def __init__(self, identifier, defineName = None, defineValue = None):
		self.identifier  = identifier
		self.targetName  = defineName
		self.targetValue = defineValue
	
# WizardAnnotations 解析器	bfs 实现
class ConfigurationWizard:
	def __init__(self, file):
		self.file = file
		self.root = ConfigurationNode("R", None)  # 根节点
		self.curNode = self.root
		self.curNode.describe(f"{file}")
		self.list = []

	def getRoot(self):
		return self.root
	
	def parseAnnotations(self):
		tokenRegex = "|".join("(?P<%s>%s)" % pair for pair in tokenSpecification)
		lineNum = 0
		lineOffset = 0
		skipToken = -0xf0		# | 标志位 | <- -0xf0 -> | 记录 <c?> | <- 0 -> | 保存跳过 token 个数 |
								# -0xf0:不在区域内		-0xf1: 在区域内		-0xf2:跳过该行全部节点的创建
		with open(self.file, "r") as f:
			nodeSlot = []
			for line in f.readlines():
				lineNum += 1			
				i = 0
				skipToken = 0 if skipToken == -0xf2 else skipToken
				for matchObj in re.finditer(tokenRegex, line):
					kind = matchObj.lastgroup
					if skipToken > 0:	# 跳过该行接下来的 token (用于<?.x>)// 不建议使用
						skipToken -= 1
						skipToken = -0xf1 if skipToken == 0 else skipToken
						continue
					elif -0xf0 < skipToken and skipToken < 0:	# 保存 <c> 的状态
						skipToken += 1	
						if skipToken == 0:
							skipToken = -0xf1
							self.curNode.bindingDefineValue = 0 if kind == "DEFINE" else 1
					thisToken = matchObj.group()
					startCol = matchObj.start() - lineOffset
					endCol = matchObj.end() - lineOffset
					# 寻找起止符
					if kind == "REGIONSTART":
						skipToken = -0xf1
						continue
					elif kind == "REGIONEND":
						skipToken = -0xf0
					if skipToken == -0xf0:
						continue 
					# 解析 token
					if i == 0 and kind == "DEFINE":
						expr = re.search(r"([\S]{1,}?)[ \t]{1,}?((L{0,1}\".*\")|([\S]{1,}))", line[endCol:])	# 宏名、宏值不允许有空格(又不是函数要什么空格)
						_defineName = str(expr.group(1))
						_defineValue = str(expr.group(2))
						if nodeSlot.__len__() != 0:
							for _node in nodeSlot:
								_node.bindingDefineName  = _defineName 
								_node.bindingDefineValue = _defineValue
						nodeSlot.clear()
						continue
					elif i == 0 and kind != "STARTFLAG":
						raise RuntimeError(f"必须以注释符(//)开始 {self.file}:{lineNum}")
					elif kind == "HEADING":
						thisNode = ConfigurationNode("h", self.curNode)
						thisNode.describe(re.findall(r"[^<\n]*", line[endCol:])[0])
						self.curNode.addChild(thisNode)
						self.curNode = thisNode
						continue
					elif kind == "CHECKHEADING":
						thisNode = ConfigurationNode("e", self.curNode)
						thisNode.describe(re.findall(r"[^<\n]*", line[endCol:])[0])
						nodeSlot.append(thisNode)
						self.curNode.addChild(thisNode)
						self.curNode = thisNode
						if "." in thisToken:
							expr = re.findall(r"\.[0-9][0-9]*", thisToken)[0][1:]
							a = int(expr)
							thisNode.mask = (1 << a)
						else:
							thisNode.mask = 1
							# print("mask:{:b}".format(thisNode.mask))
						continue
					elif kind == "NUMOPT":
						if skipToken == -0xf2:
							thisNode = self.curNode.childNodeTree[-1]
						else:
							skipToken = -0xf2
							thisNode = ConfigurationNode("o", self.curNode)
							thisNode.describe(re.findall(r"[^<\n]*", line[endCol:])[0])
							thisNode.step = 1
							nodeSlot.append(thisNode)
							self.curNode.addChild(thisNode)
						# 匹配变体 <on> <on.i> <o.i> <o.x..y>
						expr = re.findall(r"[0-9.]*", line[startCol + 2 : endCol])[0]
						if len(expr) != 0:
							# <on
							if expr[0] != ".":
								thisNode.skipDefine = int(re.findall(r"[0-9]*", expr)[0])
							# <on.x..y
							expr = re.findall(r"\.[0-9][0-9]*", expr)
							match len(expr):
								case 0:
									pass
								case 1:  # .x
									a = int(expr[0][1:])
									thisNode.mask = thisNode.mask | (1 << a)
									# print("mask:{:b}".format(thisNode.mask))
								case 2:  # .x..y
									if line[startCol + 2 : endCol].find("..") == -1:
										raise RuntimeError(f"语法错误 {self.file}:{lineNum}")
									a = int(expr[0][1:])
									b = int(expr[1][1:])
									if a > b:
										c = a
										a = b
										b = c
									while a <= b:
										thisNode.mask |= 1 << a
										a += 1
									# print("mask:{:b}".format(thisNode.mask))
									pass
								case _:
									raise RuntimeError(f"语法错误 {self.file}:{lineNum}")

						continue
					elif kind == "HELPINFO":
						thisNode = ConfigurationNode("i", self.curNode)
						thisNode.describe(re.findall(r"[^<\n]*", line[endCol:])[0])
						if len(self.curNode.childNodeTree) == 0:
							self.curNode.addInfo(re.findall(r"[^<\n]*", line[endCol:])[0])
						else:
							self.curNode.childNodeTree[-1].addInfo(re.findall(r"[^<\n]*", line[endCol:])[0])
						continue
					elif kind == "STRING":
						thisNode = ConfigurationNode("s", self.curNode)
						thisNode.describe(re.findall(r"[^<\n]*", line[endCol:])[0])
						nodeSlot.append(thisNode)
						self.curNode.addChild(thisNode)
						continue
					elif kind == "RANGEMODIFIER":
						# 匹配范围控制
						### 讨厌这个正则表达式 就能不能和vscode正则一致么 🤬艹艹艹艹艹
						_rangeToken = [
							("lower", r"((<)(([0-9]{1,}\.{1}[0-9]{1,})|[0-9]*))"),
							("upper", r"(((\.)|(-))(([0-9]{1,}\.{1}[0-9]{1,})|[0-9]{1,}))"),
							("step", r"((:)(([0-9]{1,}\.[0-9]{1,})|[0-9]{1,}))"),
						]
						rangeRegex = "|".join("(?P<%s>%s)" % pair for pair in _rangeToken)
						for matchObj in re.finditer(rangeRegex, thisToken):
							rangeKind = matchObj.lastgroup
							rangeToken = matchObj.group()
							value = rangeToken[1:]
							value = float(value) if "." in value else int(value)
							match rangeKind:
								case "upper":
									thisNode.upperLimit = value

								case "lower":
									thisNode.lowerLimit = value

								case "step":
									thisNode.step = value
						# if ("." in thisNode.upperLimit or "." in thisNode.lowerLimit) and thisNode.step is None:
						# 	thisNode.step = 1e-10
						if (isinstance(thisNode.upperLimit, int) and isinstance(thisNode.lowerLimit, int)) and thisNode.step is None:
							thisNode.step = int(1)
						# print(f"{thisNode.lowerLimit}:{thisNode.step}:{thisNode.upperLimit}")
						continue
					elif kind == "NOTIFICATION":
						thisNode = ConfigurationNode("n", self.curNode)
						thisNode.describe(re.findall(r"[^<\n]*", line[endCol:])[0])
						self.curNode.addChild(thisNode)
						continue
					elif kind == "ESCAPE":
						if self.curNode.identifier in thisToken:
							if self.curNode.identifier == "c" :
								self.curNode.bindingDefineValue ^= self.curNode.mask
							self.curNode = self.curNode.lastNode

						else:
							raise RuntimeError(f"无法匹配对应起始符 {file}:{lineNum}")
					elif kind == "CODEENABLE" :
						thisNode = ConfigurationNode("c", self.curNode)
						thisNode.describe(re.findall(r"[^<\n]*", line[endCol:])[0])
						self.curNode.addChild(thisNode)
						self.curNode = thisNode
						# 判断是正选还是负选
						if "!" in thisToken:
							self.mask = 1
						else:
							self.mask = 0
						# 判断跳过行数
						value = re.search("c[0-9]*", thisToken)[0][1:]
						self.skipItem = int(value) if len(value) > 0 else 0
						skipToken = -self.skipItem
						continue
					elif kind == "FLAG":
						thisNode = ConfigurationNode("q", self.curNode)
						nodeSlot.append(thisNode)
						thisNode.describe(re.findall(r"[^<\n]*", line[endCol:])[0])
						self.curNode.addChild(thisNode)
						thisNode.mask = 1
					elif kind == "SYMBOL_NUMBER":
						thisNode = ConfigurationNode("y", self.curNode)
						nodeSlot.append(thisNode)
						thisNode.describe(re.findall(r"[^<\n]*", line[endCol:])[0])
						self.curNode.addChild(thisNode)
						continue
					elif kind == "LISTITEM":
						if self.curNode.childNodeTree.__len__ != 0:
							_node = self.curNode.childNodeTree[-1]
						else :
							_node = self.curNode
						_node.comboListValue.append(re.search(r"<((([0-9]{1,}\.{1}[0-9]{1,})|[0-9]*)|[\S]*?)=>", thisToken).group(1))
						_node.comboListName.append(re.findall(r"[^<\n]*", line[endCol:])[0])
						continue
					elif kind == "DEFAULT":
						string = re.findall(r" *([\S ]*)", line[endCol:])[0]
						if len(self.curNode.childNodeTree)  != 0:
							self.curNode.childNodeTree[-1].default = string
						else:
							self.curNode.default = string
						continue
					else:
						pass
					i += 1
		f.close()
		if self.curNode != self.root:
			raise RuntimeError("配置信息已读取，但对应 Token 结束符")
		if skipToken != -0xf0:
			raise RuntimeError("配置信息已读取，但缺少区域结束标志")

	def __getListItemFormTree(self, thisNode:ConfigurationNode):
		if thisNode.identifier == "h" or thisNode.identifier == "R":
			for childNode in thisNode.childNodeTree:
				self.__getListItemFormTree(childNode)
		elif thisNode.identifier == "e" :
			self.list.append(ConfigurationListItem(thisNode.identifier, thisNode.bindingDefineName, thisNode.bindingDefineValue))
			for childNode in thisNode.childNodeTree:
				self.__getListItemFormTree(childNode)
		elif thisNode.identifier == "o" :
			self.list.append(ConfigurationListItem(thisNode.identifier, thisNode.bindingDefineName, thisNode.bindingDefineValue))
		elif thisNode.identifier == "q":
			self.list.append(ConfigurationListItem(thisNode.identifier, thisNode.bindingDefineName, thisNode.bindingDefineValue))
		elif thisNode.identifier == "s":
			self.list.append(ConfigurationListItem(thisNode.identifier, thisNode.bindingDefineName, thisNode.bindingDefineValue))
		elif thisNode.identifier == "y":
			self.list.append(ConfigurationListItem(thisNode.identifier, thisNode.bindingDefineName, thisNode.bindingDefineValue))
		else:
			pass
	
	def toList(self):
		self.list.clear()
		self.__getListItemFormTree(self.root)
		return self.list

class Writer:
	def __init__(self, path):
		if path is None:
			raise RuntimeError(f"路径为空")
		
		self.path = path

	def writeFile(self, list:list[ConfigurationListItem]):
		index = 0
		with open(self.path, "r") as originalFile:
			with open(f"{self.path}.h", "w") as newlFile:
				for line in originalFile.readlines():
					if index < len(list):
						match = re.search(f".*#define {{1,}}{list[index].targetName}", line)
						if match:
							newlFile.write(f"#define {list[index].targetName} {list[index].targetValue}\n")
							index += 1
							continue
					newlFile.write(line)
				originalFile.close()
				newlFile.close()
				if SafeMode == 1:
					if os.path.exists(f"{self.path}.bak"):
						os.remove(f"{self.path}.bak")
					os.rename(self.path, f"{self.path}.bak")
					os.rename(f"{self.path}.h", self.path)
				else:
					os.remove(self.path)
					os.rename(f"{self.path}.h", self.path)
		
# 重写的 widgets
class MyValidator(QValidator):
	def __init__(self, node, parent=None):
		super().__init__(parent)
		self.node = node

class MyTreeWidgetItem(QtWidgets.QTreeWidgetItem):
	def __init__(self, node:ConfigurationNode, parent=None):
		if node.identifier == "R":
			super().__init__(["打开的配置文件", f"{node.description}"])
		else:
			super().__init__([f"{node.description}"])
		self.node = node
		self.enable = True
	
	def setEnable(self, bool):
		self.enable = bool

class MySpinBox(QtWidgets.QSpinBox):
	def __init__(self, node:ConfigurationNode, parent=None):
		super().__init__(parent)
		self.node = node
		self.setFixedWidth(int(WizardTreeViewer.viewerTree.columnWidth(1)*0.5))
		if node.lowerLimit is not None:
			self.setMinimum(node.lowerLimit)
			self.setMaximum(node.upperLimit)
		else:
			self.setMinimum(-(0x10000000-1))
			self.setMaximum(0x10000000)
		if node.step is not None:	
			self.setSingleStep(node.step)
		if "0x" in node.bindingDefineValue:
			self.setDisplayIntegerBase(16)
			self.setPrefix("0x")
			self.setValue(int(node.bindingDefineValue, 16))
		else:
			self.setValue(int(node.bindingDefineValue))
		self.valueChanged.connect(self.onValueChanged)
	
	def onValueChanged(self):
		self.node.bindingDefineValue = str(self.value())
		WizardTreeViewer.slider.setValue(self.value())

	def validate(self, input, pos):
		match = re.search("(^[0-9]*$)|(^0x[0-9a-fA-F]*$)", input)
		if match:
			if input == "" or input == "0x":
				return QValidator.Intermediate
			elif (int(input,16) if "0x" in input else int(input)) < self.minimum():
				return QValidator.Intermediate
			else:
				return QValidator.Acceptable
		else:
			return QValidator.Invalid
			
	def valueFromText(self, text):
		if "0x" in text:
			return int(text,16)
		else:
			self.setDisplayIntegerBase(10)
			return int(text)

	def focusOutEvent(self, event):
		if self.value() % self.singleStep() != 0:
			self.setValue(self.value() - self.value() % self.singleStep())
		return super().focusOutEvent(event)
	
	def keyReleaseEvent(self, event):
		if event.key() == Qt.Key_Return:
			self.clearFocus()
		return super().keyReleaseEvent(event)

class MyDoubleSpinBox(QtWidgets.QDoubleSpinBox):	### TODO ❗ 待完善 ❗ 不稳定 ❗ 未测试 ❗
	def __init__(self, node:ConfigurationNode, parent=None):
		super().__init__(parent)
		self.node = node
		self.setFixedWidth(int(WizardTreeViewer.viewerTree.columnWidth(1)*0.5))
		self.setMinimum(node.lowerLimit)
		self.setMaximum(node.upperLimit)
		if node.step is not None:	
			self.setSingleStep(node.step)
		self.setDecimals(len(node.bindingDefineValue.split(".")[1]))
		self.setValue(float(node.bindingDefineValue))
		self.valueChanged.connect(self.onValueChanged)
	
	def onValueChanged(self):
		self.node.bindingDefineValue = str(self.value())
		WizardTreeViewer.slider.setValue(self.value())

	def validate(self, input, pos):
		match = re.search("^[0-9]{1,}\.[0-9]*$", input)
		if match:
			if input[pos-1] == ".":
				return QValidator.Intermediate
			else:
				return QValidator.Acceptable
		else:
			return QValidator.Invalid
	
	def valueFromText(self, text):
		self.setDecimals(len(str(text).split(".")[1]))
		return float(text)

class MySlider(QtWidgets.QSlider):
	def __init__(self, parent=None):
		super().__init__(parent)
		self.node = None
		self.Spinbox = None
		self._height = int(Configuration_Wizard_GUI.font_size * 1.4)
		self.lastPos = 0
		self._baseX = None
		self._baseY = None
		self.tickSpacing = 0
		
		# 初始化 UI
		self.setOrientation(Qt.Orientation.Horizontal)
		self.setTickPosition(QtWidgets.QSlider.TickPosition.TicksBothSides)
		self.valueChanged.connect(self.onValueChanged)
		## 构造游标
		self.label = QtWidgets.QLabel()
		### 游标 UI 初始化
		self.label.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint|Qt.CustomizeWindowHint)
		self.label.setVisible(False)
		# QWidget.des
		# value = int(Configuration_Wizard_GUI.font_size * 1.1)
		# self.label.setFixedSize(int(1.2*value), value)
		# self.wightBias = int(value)
		# self.label.setFixedWidth(self.label.fontMetrics().boundingRect(self.label.text()).width() + self.wightBias)
		
		### 游标位置信息

		self.hide()

	def bindSpinbox(self, _spinbox: MySpinBox):
		self.Spinbox = _spinbox

	def bindNode(self, node: ConfigurationNode):
		self.node = node
		self.setMinimum(node.lowerLimit)
		self.setMaximum(node.upperLimit)
		self.setSingleStep(int(node.step))
		self.setPageStep(int(node.step))
		self.setValue(int(node.bindingDefineValue))
		self.setFixedHeight(self._height)
		self.valueChanged.connect(self.onValueChanged)
	
	def unbind(self):
		self.valueChanged.disconnect()
		self.node = None
		self.Spinbox = None

	def hide(self):
		self.setFixedHeight(0)

	def mousePressEvent(self, ev):
		self.setValueFromCursor(ev.position().x())
		self.tickSpacing = self.width() / ((self.maximum() - self.minimum())/ self.singleStep())
		if 'linux' not in sys.platform:	# wsl 远程桌面无法定位
			self.initPosition()
		return super().mousePressEvent(ev)		

	def mouseMoveEvent(self, ev):
		self.setValueFromCursor(ev.position().x())
		if 'linux' not in sys.platform:
			self.setLabelPosition()

	def mouseReleaseEvent(self, ev):
		self.label.setVisible(False)
		# 当 singleStep 不为 1 时，滑动块步长仍为 1 (未知 bug) - 离散化操作
		if self.value() % self.singleStep() != 0:
			self.setValue(self.value() - self.value() % self.singleStep())
		return super().mouseReleaseEvent(ev)

	def initPosition(self):
		self._baseX = QtGui.QCursor.pos().x() - ((self.value() - self.minimum()) * self.tickSpacing)
		self._baseY = QtGui.QCursor.pos().y() - self.size().height() * 1.5
		self.setLabelPosition()
		self.label.setVisible(True)

	def setLabelPosition(self):
		self.label.setFixedWidth(self.label.fontMetrics().boundingRect(self.label.text()).width() + int(Configuration_Wizard_GUI.font_size * 0.5))
		x = self._baseX - self.label.size().width() * 0.5 + ((self.value() - self.minimum()) *  self.tickSpacing)
		print(((self.value() - self.minimum()) *  self.tickSpacing))
		self.label.move(x, self._baseY)

	def setValueFromCursor(self, locX):
		if abs(locX - self.lastPos) > self.tickSpacing:
			self.lastPos = locX
			c = 0.005
			per = ((locX / self.width()) - c) / (1 - c)
			value = per * (self.maximum() - self.minimum()) + self.minimum()
			self.setValue(value)

	def onValueChanged(self):
		# 不设置节点值 传递给 pinbox 设置
		self.label.setText(f"  {self.value()}")
		if self.Spinbox is not None:
			self.Spinbox.setValue(self.value())

class MyTextEditer(QtWidgets.QLineEdit):
	def __init__(self, node, parent=None):
		super().__init__(parent)
		self.node = node
		self.setFixedWidth(int(WizardTreeViewer.viewerTree.columnWidth(1)*0.5))
		self.setText(node.bindingDefineValue)

class MyCheckBox(QtWidgets.QCheckBox):
	def __init__(self, node:ConfigurationNode, item:MyTreeWidgetItem = None  ,parent=None):
		super().__init__(parent)
		self.node = node
		node.bindingDefineValue = int(node.bindingDefineValue)
		self.treeItem = item
		if item is not None:
			self.changeTreeItemState()
		# TODO windows 下无法修改 CheckBox 大小
		if node.bindingDefineValue:
			self.setCheckState(Qt.CheckState.Checked)
		else:
			self.setCheckState(Qt.CheckState.Unchecked)
		self.stateChanged.connect(self.onCheckboxChange)
			
	def onCheckboxChange(self):
		self.node.bindingDefineValue ^= self.node.mask
		if self.treeItem is not None:
			self.changeTreeItemState()

	def changeTreeItemState(self):
		self.treeItem.enable = bool(self.node.bindingDefineValue != 0)
		self.treeItem.setExpanded(self.treeItem.enable)
		self.treeItem.setDisabled(not self.treeItem.enable)

class MyInfoBar(QtWidgets.QTextEdit):
	def __init__(self, parent=None):
		super().__init__(parent)
		self.textChanged.connect(self.adjustHeight)

	def adjustHeight(self):
		if self.toPlainText() == "":
			self.setFixedHeight(0)
		else:
			self.setFixedHeight(self.document().size().height())

class MyComboBox(QtWidgets.QComboBox):
	def __init__(self, node:ConfigurationNode, parent=None):
		super().__init__(parent)
		self.node = node
		self.addItems(node.comboListName)
		self.setCurrentIndex(node.comboListValue.index(node.bindingDefineValue))
		self.currentIndexChanged.connect(self.onIndexChanged)
		self.setFixedWidth(int(WizardTreeViewer.viewerTree.columnWidth(1)*0.5))

	def onIndexChanged(self):
		index = self.node.comboListName.index(self.currentText())
		self.node.bindingDefineValue = self.node.comboListValue[index]
		self.hidePopup()

## 树状主视图
class WizardTreeViewer(QtWidgets.QTreeWidget):
	viewerTree = None
	sliderBar = None
	infoFormat = r" 宏定义: {name:20s}默认值: {default:20s}输入范围: {range:30s}"

	def __init__(self, mainWindow, parent=None):
		super().__init__(parent)
		self.fatherWindow = mainWindow
		WizardTreeViewer.viewerTree = self
		self.root = None
		# 初始化 TreeView 容器
		self.setColumnCount(4)
		self.setColumnHidden(2,True)       # 存放 info
		self.setColumnHidden(3,True)       # 存放 info
		self.setHeaderLabels(["Option", "Value"])
		self.setColumnWidth(0, int(mainWindow.width * 0.3))
		self.setColumnWidth(1, int(mainWindow.width * 0.6)) # 留一部分给边框
		self.selectionModel().currentChanged.connect(self.onFocusedItemChanged)

		## 样式表
		boxSize = mainWindow.default_font_size
		self.setStyleSheet(styleSheet.format(boxSize = boxSize))

		## 初始化递归辅助变量
		self.curTreeItem = None
		mainWindow.layout.addWidget(self)

		# 初始化滑动条
		self.slider = MySlider()
		WizardTreeViewer.slider = self.slider
		mainWindow.layout.addWidget(self.slider)

		# 初始化 infoBar
		self.infoBar = MyInfoBar()
		mainWindow.layout.addWidget(self.infoBar)
		self.infoBar.setFixedHeight(0)
		self.infoBar.setReadOnly(True)
		self.infoBar.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

		# 初始化状态条
		self.fatherWindow.statusBar().showMessage(WizardTreeViewer.infoFormat.format(name="None",default="None",range="None"))
		self.itemExpanded.connect(self.expandItem)
		
	def creatTreeview(self, root: ConfigurationNode):
		if len(root.childNodeTree) == 0:
			print("未能读取到 Configuration Wizard Annotations 配置信息")
			print(f"当前选定文件:{root.description}")
		else:
			self.__addItem(root)
		self.setExpandAll()

	def __addItem(self, node: ConfigurationNode):
		if node.identifier == "R":
			## 初始化根条目
			self.root = MyTreeWidgetItem(node)
			self.addTopLevelItem(self.root)
			self.curTreeItem = self.root
			node.bindTreeViewItem(self.curTreeItem)
			self.addHelpInfo(self.root, node)
			for childItem in node.childNodeTree:
				self.__addItem(childItem)
		elif node.identifier == "h":
			itemChild = MyTreeWidgetItem(node)
			node.bindTreeViewItem(itemChild)
			self.addHelpInfo(itemChild, node)
			self.curTreeItem.addChild(itemChild)
			# 添加子节点
			_curNode = self.curTreeItem
			self.curTreeItem = itemChild
			for childItem in node.childNodeTree:
				self.__addItem(childItem)
			self.curTreeItem = _curNode
		elif node.identifier == "e" or node.identifier == "c":
			itemChild = MyTreeWidgetItem(node)
			
			itemChild.treeWidget
			node.bindTreeViewItem(itemChild)
			self.addHelpInfo(itemChild, node)
			self.curTreeItem.addChild(itemChild)
			# 添加复选框
			widget = MyCheckBox(node, itemChild)
			self.setItemWidget(itemChild, 1, widget)
			# 添加子节点
			_curNode = self.curTreeItem
			self.curTreeItem = itemChild
			for childItem in node.childNodeTree:
				self.__addItem(childItem)
			self.curTreeItem = _curNode    
		elif node.identifier == "o":
			itemChild = MyTreeWidgetItem(node)
			node.bindTreeViewItem(itemChild)
			self.curTreeItem.addChild(itemChild)
			self.addHelpInfo(itemChild, node)
			widget = None
			if len(node.comboListValue) != 0:
				widget = MyComboBox(node)
			elif node.mask != 0:
				widget = MyCheckBox(node)
			elif isinstance(node.step, int) and "." not in node.bindingDefineValue:
				widget = MySpinBox(node)
			else:
				widget = MyDoubleSpinBox(node)

			self.setItemWidget(itemChild, 1, widget)
		elif node.identifier == "n":
			itemChild = MyTreeWidgetItem(node)
			node.bindTreeViewItem(itemChild)
			self.addHelpInfo(itemChild, node)
			self.curTreeItem.addChild(itemChild)
		elif node.identifier == "q":
			itemChild = MyTreeWidgetItem(node)
			node.bindTreeViewItem(itemChild)
			self.addHelpInfo(itemChild, node)
			self.curTreeItem.addChild(itemChild)
			widget = MyCheckBox(node)
			self.setItemWidget(itemChild, 1, widget)
		elif node.identifier == "s":
			itemChild = MyTreeWidgetItem(node)
			node.bindTreeViewItem(itemChild)
			self.addHelpInfo(itemChild, node)
			self.curTreeItem.addChild(itemChild)
			widget = MyTextEditer(node)
			self.setItemWidget(itemChild, 1, widget)
		elif node.identifier == "y":
			itemChild = MyTreeWidgetItem(node)
			node.bindTreeViewItem(itemChild)
			self.addHelpInfo(itemChild, node)
			self.curTreeItem.addChild(itemChild)
			widget = MyTextEditer(node)
			widget.setFixedWidth(int(self.columnWidth(1)*0.3))
			self.setItemWidget(itemChild, 1, widget)
		else:
			pass

	def addHelpInfo(self, treeItem:MyTreeWidgetItem, node):
		if (node.lowerLimit is None) or (node.step is None) or (node.upperLimit is None):
			_range = "None"
		else:
			_range = f"0x{node.lowerLimit:08x} : {node.step} : 0x{node.upperLimit:08x}"
		treeItem.setData(2,Qt.ItemDataRole.StatusTipRole, WizardTreeViewer.infoFormat.format(name=str(node.bindingDefineName),default=str(node.default),range=_range))
		if node.helpInfo.__len__ != 0:
			string = ""
			for info in node.helpInfo:
				string = string + info + "\n"
		treeItem.setData(3,Qt.ItemDataRole.StatusTipRole, string[:-1])  # 不要最后一个换行
	  
	def onFocusedItemChanged(self, current, previous):
		info = self.itemFromIndex(current).data(2,Qt.ItemDataRole.StatusTipRole)
		message = self.itemFromIndex(current).data(3,Qt.ItemDataRole.StatusTipRole)
		self.infoBar.setText(message)
		self.fatherWindow.statusBar().showMessage(f"{info}")
		if self.slider.node is not None:
			self.slider.unbind()
		if (self.itemFromIndex(current).node.identifier == "o" and self.itemFromIndex(current).node.lowerLimit is not None and self.itemFromIndex(current).node.step is not None):
			if((self.itemFromIndex(current).node.upperLimit - self.itemFromIndex(current).node.lowerLimit)/self.itemFromIndex(current).node.step < 0x00010000):														
				self.slider.bindNode(self.itemFromIndex(current).node)				# 过多刻度会使 qt 卡死 而且过大的数字也没必要用滑动条了
				self.slider.bindSpinbox(self.itemWidget(self.itemFromIndex(current), 1))
				return
		self.slider.hide()

	def expandItem(self, item:MyTreeWidgetItem):
		for i in range(0, item.childCount()):
			self.expandItem(item.child(i))
		item.setExpanded(item.enable)

	def setExpandAll(self):
		self.expandItem(self.root)

# 主窗口
class Configuration_Wizard_GUI(QMainWindow):
	file = None
	font_size = None
	def __init__(self, *args, **kwargs):
		super(Configuration_Wizard_GUI, self).__init__(*args, **kwargs)
		self.__screen__ = QGuiApplication.primaryScreen().size()
		self.WizardTreeViewer = None
		self.root = None
		self.currentFile = None

		# # 格式化传入路径
		# if 'linux' in sys.platform:
		# 	self.currentFile = self.passinaFile
		# elif 'win32' in sys.platform:
		# 	self.currentFile = self.passinaFile
		# else:
		# 	print(sys.platform)
		
		# 初始化内部变量
		self.width = int(app.primaryScreen().size().width() / 1.5)
		self.height = int(app.primaryScreen().size().height() / 1.5)
		self.default_font_size = int(self.height / 40)
		Configuration_Wizard_GUI.font_size = self.default_font_size
		self.default_font = QtGui.QFont(userFont, pointSize=self.default_font_size + 10)
		# self.default_font.setFamily("Microsoft Yahei UI")
		self.setStyleSheet(f"font: {self.default_font_size}px; background: 0x404040")
		self.setFont(self.default_font)
		self.mainWidget = QWidget()
		self.setCentralWidget(self.mainWidget)
		self.layout = QVBoxLayout(self.mainWidget)

		# 初始化主窗口
		self.setWindowTitle("CMSIS Configuration Wizard Annotations GUI")
		self.setMinimumSize(int(self.default_font_size * 25), int(self.default_font_size * 25))
		self.resize(self.width, self.height)
		
		# 初始化UI
		self.set_menuBar()
		if passinaFile is not None:
			self.currentFile = passinaFile
			self.creatTreeView(self.currentFile)
		self.show()

	def set_menuBar(self):
		# 初始化菜单栏
		menuBar = QtWidgets.QMenuBar(self)
		menuBar.setFont(self.default_font)
		self.setMenuBar(menuBar)
		self.layout.addWidget(menuBar)

		## 初始化菜单栏->文件
		subMenu = QtWidgets.QMenu("\udb80\ude4b 文件", menuBar)
		subMenu.setFont(self.default_font)
		menuBar.addMenu(subMenu)
		### 初始化菜单栏->文件->打开文件
		action = QAction("打开文件", self)
		action.triggered.connect(self.select_file)
		action.setShortcut("Ctrl+O")
		subMenu.addAction(action)
		### 初始化菜单栏->文件->保存文件
		action = QAction("保存", self)
		action.triggered.connect(self.saveFile)
		action.setShortcut("Ctrl+S")
		subMenu.addAction(action)
		### 初始化菜单栏->文件->另存文件
		action = QAction("另存为", self)
		# action.triggered.connect(self.select_file)
		action.setShortcut("Ctrl+Shift+S")
		subMenu.addAction(action)
		subMenu.addSeparator()
		### 初始化菜单栏->文件->撤销
		action = QAction("撤销", self)
		# action.triggered.connect(self.select_file)
		action.setShortcut("Ctrl+Z")
		subMenu.addAction(action)
		### 初始化菜单栏->文件->重做
		action = QAction("重做", self)
		# action.triggered.connect(self.select_file)
		action.setShortcut("Ctrl+Y")
		subMenu.addAction(action)
		subMenu.addSeparator()
		### 初始化菜单栏->文件->关闭窗口
		action = QAction("关闭窗口", self)
		action.triggered.connect(self.close)
		action.setShortcut("Alt+F4")
		subMenu.addAction(action)

		## 初始化菜单栏->关于
		subMenu = QtWidgets.QMenu("\ueb32 关于", menuBar)
		subMenu.setFont(self.default_font)
		menuBar.addMenu(subMenu)

		action = QAction("关于作者", self)
		subMenu.addAction(action)
		action.triggered.connect(self.show_about)
		# action.setShortcut("Alt+F4")
		
	def select_file(self):
		# QFileDialog组件定义
		fileDialog = QtWidgets.QFileDialog(self)
		fileDialog.setFont(self.default_font)
		fileDialog.setBaseSize(self.default_font_size * 50, self.default_font_size * 50)
		# QFileDialog组件设置
		fileDialog.setWindowTitle("选择配置头文件")
		fileDialog.setFileMode(QtWidgets.QFileDialog.AnyFile)
		fileDialog.setDirectory(os.getcwd())
		fileDialog.setNameFilter("头文件 (*.h);;All files (*)")
		fileDialog.resize(int(self.width * 0.7), int(self.height * 0.7))
		file_path = fileDialog.exec()
		if file_path and fileDialog.selectedFiles():
			self.creatTreeView(fileDialog.selectedFiles()[0])

	def creatTreeView(self, path):
		self.currentFile = path
		self.wizard = ConfigurationWizard(self.currentFile)
		self.wizard.parseAnnotations()
		self.root = self.wizard.getRoot()
		self.WizardTreeViewer = WizardTreeViewer(self)
		self.WizardTreeViewer.creatTreeview(self.root)

	def show_about(self):
		# 初始化内部对话框
		dialog = QtWidgets.QDialog(self)
		dialog.setWindowTitle("关于作者")
		dialog.resize(600, 400)
		dialog.setFont(self.default_font)
		QtWidgets.QLabel("Reglucis", dialog).setFont(self.default_font)
		dialog.exec()

	def saveFile(self):
		if self.currentFile is None:
			return
		list = self.wizard.toList()
		Writer(self.currentFile).writeFile(list)

if __name__ == "__main__":
	app = QApplication(sys.argv)
	window = Configuration_Wizard_GUI()
	sys.exit(app.exec())

