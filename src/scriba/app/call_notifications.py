"""WinRT actions for recording toasts, using the shared toast notifier."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from xml.sax.saxutils import escape, quoteattr

from scriba.app import wintoast as win
from scriba.app.log import complain

# IToastActivatedEventArgs inherits IInspectable. get_Arguments is its
# first method in windows.ui.notifications.h.
_EVENT_ARGS = win._guid("e3bf92f3-c197-436f-8265-0625824f8dac")
_GET_ARGUMENTS = 6
_raw_buffer = win.combase.WindowsGetStringRawBuffer
_raw_buffer.argtypes = [win.HSTRING, ctypes.POINTER(wintypes.UINT)]
_raw_buffer.restype = ctypes.c_void_p


def xml(title, actions):
    buttons = "".join(
        f'<action content={quoteattr(label)} arguments={quoteattr(key)} activationType="foreground"/>'
        for key, (label, _callback) in actions.items()
    )
    return ('<toast><visual><binding template="ToastGeneric">'
            f"<text>{escape(title)}</text></binding></visual><actions>{buttons}</actions></toast>")


def _argument(args):
    event = win._query(args, _EVENT_ARGS)
    text = win.HSTRING()
    try:
        win._call(event, _GET_ARGUMENTS, ctypes.byref(text), argtypes=(ctypes.POINTER(win.HSTRING),))
        length = wintypes.UINT()
        buffer = _raw_buffer(text, ctypes.byref(length))
        return ctypes.wstring_at(buffer, length.value) if buffer else ""
    finally:
        win.combase.WindowsDeleteString(text)
        win._release(event)


class _ActionHandler(win._ActivatedHandler):
    def __init__(self, actions):
        self.actions = actions
        super().__init__(None)

    def _invoke(self, _this, _sender, args):
        try:
            action = self.actions.get(_argument(args)) if args else None
            if action is not None:
                action[1]()
        except Exception as error:  # the COM callback cannot propagate an exception
            complain("handling a notification action", error)
        return win.S_OK


def show(toasts, title, actions, *, tag="call-ended"):
    """Show the choice and retain its handler as long as the notifier needs it."""
    document = ctypes.c_void_p()
    with win._HString("Windows.Data.Xml.Dom.XmlDocument") as name:
        win._check(win.combase.RoActivateInstance(name, ctypes.byref(document)), "XmlDocument")
    doc_io = doc_xml = toast = properties = None
    try:
        doc_io = win._query(document, win.IID_IXmlDocumentIO)
        with win._HString(xml(title, actions)) as body:
            win._call(doc_io, win.XMLIO_LOAD_XML, body, argtypes=(win.HSTRING,))
        doc_xml = win._query(document, win.IID_IXmlDocument)
        toast = ctypes.c_void_p()
        win._call(toasts._toast_factory, win.FACTORY_CREATE, doc_xml, ctypes.byref(toast),
                  argtypes=(ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)))
        properties = win._query(toast, win.IID_IToastNotification2)
        with win._HString(tag) as toast_tag, win._HString(toasts.group) as group:
            win._call(properties, win.TOAST2_PUT_TAG, toast_tag, argtypes=(win.HSTRING,))
            win._call(properties, win.TOAST2_PUT_GROUP, group, argtypes=(win.HSTRING,))
        handler, token = _ActionHandler(actions), ctypes.c_int64()
        win._call(toast, win.TOAST_ADD_ACTIVATED, handler.pointer, ctypes.byref(token),
                  argtypes=(ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64)))
        win._call(toasts._notifier, win.NOTIFIER_SHOW, toast, argtypes=(ctypes.c_void_p,))
        toasts._kept.append((toast, handler, token.value))
        toast = None
        while len(toasts._kept) > win.KEPT_TOASTS:
            toasts._let_go(*toasts._kept.pop(0))
    finally:
        for obj in properties, toast, doc_xml, doc_io, document:
            win._release(obj)
