"""Windows spooler RAW transport for Citizen ZPL; no pywin32 dependency."""
import ctypes
from ctypes import wintypes


def _load_spooler():
    spool=ctypes.WinDLL('winspool.drv',use_last_error=True)
    handle=wintypes.HANDLE;dword=ctypes.c_uint32;boolean=wintypes.BOOL
    signatures={
        'OpenPrinterW':([wintypes.LPWSTR,ctypes.POINTER(handle),ctypes.c_void_p],boolean),
        'StartDocPrinterW':([handle,dword,ctypes.c_void_p],dword),
        'WritePrinter':([handle,ctypes.c_void_p,dword,ctypes.POINTER(dword)],boolean),
    }
    signatures.update({name:([handle],boolean) for name in ('StartPagePrinter','EndPagePrinter','EndDocPrinter','AbortPrinter','ClosePrinter')})
    for name,(args,result) in signatures.items():
        function=getattr(spool,name);function.argtypes=args;function.restype=result
    return spool


class DocumentInfo(ctypes.Structure):
    _fields_=[('pDocName',wintypes.LPWSTR),('pOutputFile',wintypes.LPWSTR),('pDatatype',wintypes.LPWSTR)]


def _error(action):
    code=getattr(ctypes,'get_last_error',lambda:0)()
    return OSError(f'{action} failed (Windows error {code}). Check the printer queue before retrying; some output may already have printed.')


def submit_windows_raw(queue,payload):
    if not queue:raise ValueError('Choose an installed Windows printer queue.')
    spool=_load_spooler();handle=wintypes.HANDLE();started=False
    if not spool.OpenPrinterW(queue,ctypes.byref(handle),None):raise _error('Opening printer')
    try:
        info=DocumentInfo('Cloth Shop Citizen Labels',None,'RAW')
        job=spool.StartDocPrinterW(handle,1,ctypes.byref(info))
        if not job:raise _error('Starting print job')
        started=True
        if not spool.StartPagePrinter(handle):raise _error('Starting label page')
        buffer=ctypes.create_string_buffer(payload);offset=0
        while offset<len(payload):
            written=ctypes.c_uint32()
            if not spool.WritePrinter(handle,ctypes.byref(buffer,offset),len(payload)-offset,ctypes.byref(written)) or not 0<written.value<=len(payload)-offset:
                raise _error('Writing labels')
            offset+=written.value
        if not spool.EndPagePrinter(handle):raise _error('Ending label page')
        if not spool.EndDocPrinter(handle):raise _error('Submitting print job')
        started=False
        return int(job)
    except Exception:
        if started:spool.AbortPrinter(handle)
        raise
    finally:
        spool.ClosePrinter(handle)
