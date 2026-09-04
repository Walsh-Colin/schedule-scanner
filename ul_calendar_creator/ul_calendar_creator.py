if __package__:
    from .ui.app import ULCalendarApp
else:
    from ui.app import ULCalendarApp


def main() -> None:
    app = ULCalendarApp()
    app.mainloop()


if __name__ == "__main__":
    main()
