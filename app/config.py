from importlib.metadata import PackageNotFoundError, version


APP_NAME = "email-hoover"


def _package_version() -> str:
    try:
        return version(APP_NAME)
    except PackageNotFoundError:
        return "0.1.0"


APP_VERSION = _package_version()
