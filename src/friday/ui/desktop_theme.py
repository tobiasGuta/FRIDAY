"""v0.5.5 hybrid-shell styling; presentation only, no runtime resources."""

STYLE = """
QMainWindow, QWidget#root, QWidget#page, QScrollArea {
    background-color: #080F1D; color: #EDF5FF;
}
QWidget { color: #EDF5FF; font-family: "Segoe UI"; font-size: 13px; }
QFrame#sidebar {
    background-color: #0A182B; border-right: 1px solid #253B58;
    border-top-left-radius: 15px; border-bottom-left-radius: 15px;
}
QFrame#topbar {
    background-color: #101F34; border: 1px solid #273E5B;
    border-radius: 14px;
}
QFrame#panel {
    background-color: #12233A; border: 1px solid #2D4A6C;
    border-radius: 16px;
}
QFrame#hero {
    background-color: #152D4E; border: 1px solid #3E72AE;
    border-radius: 18px;
}
QFrame#voiceStage {
    background-color: #102443; border: 1px solid #305A8B;
    border-radius: 22px;
}
QFrame#approval {
    background-color: #30243F; border: 1px solid #8974B8;
    border-radius: 14px;
}
QFrame#metric {
    background-color: #142C47; border: 1px solid #314F73;
    border-radius: 13px;
}
QLabel#brand { color: #EFF8FF; font-size: 24px; font-weight: bold; }
QLabel#heading { color: #F1F7FF; font-size: 25px; font-weight: bold; }
QLabel#pageTitle { color: #F1F7FF; font-size: 21px; font-weight: bold; }
QLabel#subheading { color: #A5BDD9; font-size: 12px; }
QLabel#status { color: #79F0C8; font-size: 13px; font-weight: bold; }
QLabel#section { color: #ECF6FF; font-size: 15px; font-weight: bold; }
QLabel#approvalTitle { color: #EDE1FF; font-size: 15px; font-weight: bold; }
QLabel#detail { color: #C7D8EB; font-size: 13px; }
QLabel#chip {
    background-color: #18334D; border: 1px solid #345575;
    border-radius: 9px; padding: 7px 11px; color: #CDE8F5;
}
QLabel#hint { color: #91B4D7; font-size: 12px; }
QPushButton {
    background-color: #1D3756; color: #EAF5FF;
    border: 1px solid #426183; border-radius: 10px;
    padding: 9px 14px; font-size: 13px; font-weight: bold;
}
QPushButton:hover { background-color: #2B5077; border-color: #71A0CE; }
QPushButton:pressed { background-color: #1A65A6; }
QPushButton:disabled {
    background-color: #15263A; color: #7488A1; border-color: #25384E;
}
QPushButton#nav {
    text-align: left; background-color: transparent; border: 1px solid transparent;
    padding: 13px 17px; font-size: 14px; color: #BBCFE8;
}
QPushButton#nav:hover { background-color: #173454; }
QPushButton#nav:checked {
    background-color: #19436E; border: 1px solid #3289CE;
    color: #FFFFFF;
}
QPushButton#primary {
    background-color: #287DE2; border: 1px solid #58AFFF; color: #FFFFFF;
}
QPushButton#primary:hover { background-color: #459AF2; }
QPushButton#approve {
    background-color: #1A695F; border-color: #359D8A;
}
QPushButton#reject {
    background-color: #50354A; border-color: #8A5A78;
}
QPushButton#danger {
    background-color: #4D293B; border-color: #8A4058;
}
QPlainTextEdit, QListWidget {
    background-color: #09182A; border: 1px solid #2D4765;
    color: #E2F0FF; border-radius: 11px; padding: 10px;
    selection-background-color: #205B9B;
}
QCheckBox { color: #C5D8EA; spacing: 8px; }
QCheckBox:disabled { color: #778DA5; }
QLineEdit {
    background-color: #09182A; border: 1px solid #365676;
    color: #E2F0FF; border-radius: 9px; padding: 8px 11px;
}
QLineEdit:disabled { color: #778DA5; }
QScrollArea { border: none; }
QScrollBar:vertical {
    background-color: #0D1C30; width: 11px; margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #345775; min-height: 25px; border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QToolTip {
    background-color: #142842; border: 1px solid #4972A0; color: #F1F8FF;
}
"""
