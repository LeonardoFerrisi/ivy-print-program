import win32print

flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
for info in win32print.EnumPrinters(flags):
    print(info[2])