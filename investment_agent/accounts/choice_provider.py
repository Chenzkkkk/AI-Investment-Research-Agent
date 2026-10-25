from ..common import DataError


class ChoiceAccountProvider:
    source = "CHOICE_PORTFOLIO"
    live_ready = False

    def fetch(self, target):
        raise DataError("PERMISSION_DENIED", "已有Choice诊断返回10001003；本轮不重装、不重试登录，也不将Choice组合当作网页或GM账户。")
