from stapel_core.django.api.errors import StapelServiceError

from .errors import ERR_423_PROBE_LOCKED


class LockService:
    @staticmethod
    def check(name):
        if name == "locked":
            raise StapelServiceError(423, ERR_423_PROBE_LOCKED, {"minutes": 5})
