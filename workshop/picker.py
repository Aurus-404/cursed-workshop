"""Native file dialogs owned by this app and invoked only by a GUI button."""
import sys
import tkinter as tk
from tkinter import filedialog

def main():
    root=tk.Tk(); root.withdraw(); root.attributes('-topmost',True)
    if len(sys.argv)>1 and sys.argv[1]=='directory':
        value=filedialog.askdirectory(title='Seleccionar carpeta')
    else:
        value=filedialog.askopenfilename(title='Abrir campaña de Conquista',filetypes=[('Campañas Gates of Hell','*.sav')])
    root.destroy()
    print(value,flush=True)

if __name__=='__main__': main()
