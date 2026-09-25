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
QFrame#homeVoiceHero {
    background-color: #102949; border: 1px solid #376FA8;
    border-radius: 18px;
}
QFrame#summaryRow {
    background-color: #0D2036; border: 1px solid #2E4C6C;
    border-radius: 11px;
}
QLabel#eyebrow {
    color: #81C9EA; font-size: 11px; letter-spacing: 2px; font-weight: bold;
}
QLabel#itemTitle { color: #F3F9FF; font-size: 14px; font-weight: bold; }
QLabel#summaryBadge { color: #9EDACC; font-size: 11px; font-weight: bold; }
QFrame#voiceStage {
    background-color: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                                      stop:0 #0D223E,stop:0.55 #15385D,
                                      stop:1 #0B1E37);
    border: 1px solid #376DA5; border-radius: 23px;
}
QFrame#voiceRibbon {
    background-color: #11243C; border: 1px solid #315479;
    border-radius: 13px;
}
QLabel#voiceHeadline {
    color: #F3FAFF; font-size: 23px; font-weight: bold;
}
QLabel#voiceActivity {
    color: #9BC2E4; font-size: 12px;
}
QLabel#voiceStateBadge {
    background-color: #29384E; border: 1px solid #476383;
    border-radius: 10px; padding: 7px 12px;
    color: #DCE9F6; font-weight: bold;
}
QLabel#voiceStateBadge[phase="Ready"] {
    background-color: #173950; color: #A7E8FF; border-color: #3B8AA8;
}
QLabel#voiceStateBadge[phase="Listening"] {
    background-color: #164A45; color: #A7FFE2; border-color: #3AA88E;
}
QLabel#voiceStateBadge[phase="Responding"] {
    background-color: #40365C; color: #E1D3FF; border-color: #8B79BE;
}
QScrollArea#bubbleViewport, QWidget#bubbleCanvas {
    background-color: #0B192C; border: 1px solid #284665;
    border-radius: 12px;
}
QFrame#userBubble {
    background-color: #1D426A; border: 1px solid #497FB2;
    border-radius: 13px;
}
QFrame#assistantBubble {
    background-color: #182E4A; border: 1px solid #365B82;
    border-radius: 13px;
}
QFrame#systemBubble {
    background-color: #1A283A; border: 1px solid #40546A;
    border-radius: 11px;
}
QLabel#bubbleRole { color: #88D0E9; font-size: 11px; font-weight: bold; }
QLabel#bubbleText { color: #F3F9FF; font-size: 13px; }
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
