from ..common import DataError


class EastmoneyWebProvider:
    source = "EASTMONEY_WEB_SIM"
    live_ready = False

    def fetch(self, target):
        # No undocumented requests, browser profile extraction, cookies, or hidden endpoints.
        raise DataError("MANUAL_IMPORT_ONLY", "网页只读通道尚未获网站规则和页面映射验证；未连接浏览器。可由本人提供官方数据，保持手工导入标识。")
