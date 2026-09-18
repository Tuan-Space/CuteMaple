from journal_test_support import settle_queries
import itertools
import os

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PySide6.QtCore import Qt,QDate,QRectF
from PySide6.QtGui import QColor,QPalette
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QListWidget,QListWidgetItem,QStyleOptionComboBox,QStyle

from journal_design import (MonthCalendar,TYPE_ORDER,ITEM_PRESENTATION_ROLE,WrappedItem,
                            journal_font,load_journal_fonts,palette,style)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=[False,True],ids=['light','dark'])
def calendar_view(app,request):
    window=QWidget();window._theme=palette(request.param)
    window.setFont(journal_font());window.setStyleSheet(style(window._theme))
    layout=QVBoxLayout(window);calendar=MonthCalendar();layout.addWidget(calendar)
    window.resize(450,530);window.show();app.processEvents()
    yield window,calendar
    window.close()


def test_every_marker_combination_is_centered_and_inside_day(calendar_view):
    _,calendar=calendar_view;cell=calendar.cells[0];cell.date=QDate(2026,10,1)
    for width in (54,73,100):
        cell.resize(width,62)
        for count in range(6):
            for kinds in itertools.combinations(TYPE_ORDER,count):
                calendar.marks={'2026-10-01':set(kinds)}
                markers=cell.marker_rects()
                assert [kind for kind,_ in markers]==list(kinds)
                if not markers:continue
                left=markers[0][1].left();right=markers[-1][1].right()
                assert (left+right)/2==pytest.approx(width/2)
                for _,rect in markers:
                    assert QRectF(cell.rect()).adjusted(2,2,-2,-2).contains(rect.adjusted(-.5,-.5,.5,.5))


@pytest.mark.parametrize('month',[10,11,12])
def test_two_digit_month_has_room_for_complete_label(app,calendar_view,month):
    window,calendar=calendar_view
    for size in (10,12):
        calendar.month.setStyleSheet(f'font-size:{size}pt;')
        for width in (450,700):
            window.resize(width,530);calendar.setCurrentPage(2026,month);app.processEvents()
            option=QStyleOptionComboBox();calendar.month.initStyleOption(option)
            text_rect=calendar.month.style().subControlRect(QStyle.CC_ComboBox,option,QStyle.SC_ComboBoxEditField,calendar.month)
            assert text_rect.width()>=calendar.month.fontMetrics().horizontalAdvance(calendar.month.currentText())+4


def test_list_presentation_keeps_business_payload_and_wraps(app):
    view=QListWidget();view._theme=palette();view.setFont(journal_font());view.setStyleSheet(style(view._theme))
    delegate=WrappedItem(view);view.setItemDelegate(delegate);view.resize(420,450);view.show();app.processEvents()
    item=QListWidgetItem('旧版文字\n旧版副文');item.setData(Qt.UserRole,{'id':'kept','body':'原正文'})
    view.addItem(item);index=view.model().index(0,0)
    assert delegate.presentation(index)['title']=='旧版文字'
    title='整理旅行中拍下的照片，记录每一个值得保留的小瞬间。'*3
    item.setData(ITEM_PRESENTATION_ROLE,{'title':title,'subtitle':'10月12日 09:30 · 每周','category':'schedule','status':'待处理','status_tone':'warning'})
    assert item.data(Qt.UserRole)=={'id':'kept','body':'原正文'}
    assert title in delegate.document(index,420).toPlainText()
    wide=delegate.document(index,420).size().height();narrow=delegate.document(index,190).size().height()
    assert narrow>wide
    assert [label for label,_ in delegate.badges(index)]==['日程','待处理']
    for width in (190,420):
        badges,height=delegate.badge_layout(index,width)
        assert height>0
        assert all(rect.right()<=width-32 for _,_,rect in badges)
    view.close()


def test_semantic_fonts_and_distinct_category_hues(app):
    fonts=load_journal_fonts()
    assert journal_font('body').family()==fonts['body']
    assert journal_font('title').family()==fonts['display']
    assert 'display' in fonts and fonts['display']
    for dark in (False,True):
        colors=palette(dark)
        hues={key:QColor(colors[key]).hue() for key in TYPE_ORDER}
        for a,b in itertools.combinations(('schedule','note','habit'),2):
            distance=abs(hues[a]-hues[b]);assert min(distance,360-distance)>55


@pytest.mark.parametrize('items',[
    ['正文','一级标题','二级标题','三级标题'],
    [f'{month} 月' for month in range(1,13)],
],ids=['headings','months'])
def test_combo_popup_margins_follow_theme_changes(app,items):
    from monitor_ui import ThemedComboBox
    window=QWidget();layout=QVBoxLayout(window);combo=ThemedComboBox()
    combo.addItems(items);layout.addWidget(combo);window.resize(360,500)
    window.show()
    for dark in (True,False,True):
        window._theme=palette(dark);window.setStyleSheet(style(window._theme))
        app.processEvents();combo.showPopup();app.processEvents()
        popup=combo.view().window();image=popup.grab().toImage()
        expected=QColor(window._theme['card'])
        assert popup.palette().color(QPalette.Base)==expected
        inset=round(3*image.devicePixelRatio())
        for y in (inset,image.height()-inset-1):
            # The native menu border adds a subtle gradient to its base color.
            actual=image.pixelColor(image.width()//2,y)
            assert max(abs(a-b) for a,b in zip(actual.getRgb()[:3],expected.getRgb()[:3]))<=8
        combo.hidePopup()
    window.close()


@pytest.mark.parametrize('before,target,expected',[
    (QDate(2026,10,17),(2026,11),QDate(2026,11,17)),
    (QDate(2026,1,31),(2026,2),QDate(2026,2,28)),
    (QDate(2024,1,31),(2024,2),QDate(2024,2,29)),
    (QDate(2026,12,31),(2027,1),QDate(2027,1,31)),
])
def test_changing_month_keeps_selection_in_visible_month(app,before,target,expected):
    calendar=MonthCalendar();calendar.setSelectedDate(before);changes=[]
    calendar.selectionChanged.connect(lambda:changes.append(calendar.selectedDate()))
    calendar.setCurrentPage(*target)
    assert calendar.selectedDate()==expected
    assert changes==[expected]
    calendar.setSelectedDate(QDate(2028,4,12))
    assert calendar.selectedDate()==QDate(2028,4,12)
    assert changes==[expected,QDate(2028,4,12)]
    calendar.close()


def test_month_navigation_details_match_selected_month(app,tmp_path):
    from journal_store import JournalStore
    from journal_recurrence import Rule
    from journal_ui import JournalWindow
    store=JournalStore(tmp_path/'month-navigation',create=True)
    store.save_event('十月的安排','schedule','',Rule('2026-10-17T09:00:00'))
    store.save_event('十一月的安排','schedule','',Rule('2026-11-17T09:00:00'))
    window=JournalWindow(store,None)
    window.calendar.setSelectedDate(QDate(2026,10,17))
    settle_queries();assert any(row['title']=='十月的安排' for _,row,_ in window.calendar_items['2026-10-17'])
    window.calendar.setCurrentPage(2026,11)
    assert window.calendar.selectedDate()==QDate(2026,11,17)
    assert window.day_heading.text()=='11月17日'
    settle_queries();assert any(row['title']=='十一月的安排' for _,row,_ in window.calendar_items['2026-11-17'])
    assert any('十一月的安排' in window.day_items.item(i).text() for i in range(window.day_items.count()))
    window.close();store.close()
