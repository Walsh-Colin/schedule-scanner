if __package__:
    from .ui.app import ScheduleScannerApp
else:
    from ui.app import ScheduleScannerApp

def main() -> None:
    app = ScheduleScannerApp()
    app.mainloop()

if __name__ == "__main__":
    main()
    