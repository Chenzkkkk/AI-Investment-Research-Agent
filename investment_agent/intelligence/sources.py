"""An explicit registry: a candidate is not a connected source."""
DOMAINS = ['政策与全球局势','宏观与资金','科学与技术','产品与应用','产业与供应链','公司与资本','创业与商业模式','社会与消费']
EVENT_TYPES = ['发布','研究','融资','订单','政策','数据公布','观点','预测','澄清','经营变化','其他']


def source(key, name, url, domain, region='全球', kind='rss', tier='原始发布', interval=30, pattern=''):
    return dict(id=key, name=name, url=url, domain=domain, region=region, kind=kind,
                tier=tier, interval=interval, pattern=pattern, enabled=kind!='candidate')


SOURCES = [
    source('fed-policy','美联储 · 政策','https://www.federalreserve.gov/feeds/press_monetary.xml',DOMAINS[0],'美国'),
    source('fed-speech','美联储 · 讲话','https://www.federalreserve.gov/feeds/speeches.xml',DOMAINS[1],'美国'),
    source('fed-news','美联储 · 发布','https://www.federalreserve.gov/feeds/press_all.xml',DOMAINS[1],'美国'),
    source('stats','国家统计局','https://www.stats.gov.cn/sj/zxfb/',DOMAINS[1],'中国','html',interval=120,pattern=r'/sj/zxfb/\d{6}/t\d+_\d+\.html'),
    source('nasa','NASA','https://www.nasa.gov/feed/',DOMAINS[2],interval=1440),
    source('arxiv','arXiv · AI','https://rss.arxiv.org/rss/cs.AI',DOMAINS[2],interval=1440),
    source('huggingface','Hugging Face','https://huggingface.co/blog/feed.xml',DOMAINS[2]),
    source('openai','OpenAI','https://openai.com/news/rss.xml',DOMAINS[3]),
    source('nvidia','NVIDIA','https://blogs.nvidia.com/feed/',DOMAINS[4]),
    source('github','GitHub','https://github.blog/feed/',DOMAINS[3]),
    source('ithome','IT之家','https://www.ithome.com/rss/',DOMAINS[3],'中国',tier='媒体报道'),
    source('iea','国际能源署','https://www.iea.org/news',DOMAINS[4],kind='html',interval=120,pattern=r'/news/[^/?#]+$'),
    source('sec','SEC · 公司披露','https://www.sec.gov/news/pressreleases.rss',DOMAINS[5],'美国',interval=120),
    source('anthropic','Anthropic','https://www.anthropic.com/news',DOMAINS[5],kind='html',interval=120,pattern=r'/news/[^/?#]+$'),
    source('hn','Hacker News · Show HN','https://hacker-news.firebaseio.com/v0/showstories.json',DOMAINS[6],kind='hn',tier='社区线索'),
    source('techcrunch','TechCrunch · 创业','https://techcrunch.com/category/startups/feed/',DOMAINS[6],tier='媒体报道'),
    source('who','世界卫生组织','https://www.who.int/news',DOMAINS[7],kind='html',interval=120,pattern=r'/news/item/\d{2}-\d{2}-\d{4}-'),
    source('un','联合国新闻','https://news.un.org/feed/subscribe/en/news/all/rss.xml',DOMAINS[7],interval=120),
    source('pbc','中国人民银行','https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html',DOMAINS[1],'中国','html',interval=120,pattern=r'/goutongjiaoliu/.+\.html'),
    source('ndrc','国家发展改革委','https://www.ndrc.gov.cn/xwdt/xwfb/',DOMAINS[0],'中国','html',interval=120,pattern=r'/\d{6}/t\d+_\d+\.html'),
    source('miit','工业和信息化部','https://www.miit.gov.cn/',DOMAINS[4],'中国','candidate'),
    source('cninfo','巨潮资讯 · 通用情报适配','https://www.cninfo.com.cn/',DOMAINS[5],'中国','candidate'),
    source('sse','上海证券交易所 · 通用情报适配','https://www.sse.com.cn/',DOMAINS[5],'中国','candidate'),
    source('imf','国际货币基金组织','https://www.imf.org/en/News',DOMAINS[1],kind='candidate'),
    source('worldbank','世界银行','https://www.worldbank.org/en/news',DOMAINS[7],kind='candidate'),
    source('wto','世界贸易组织','https://www.wto.org/',DOMAINS[0],kind='candidate'),
    source('yc','Y Combinator','https://www.ycombinator.com/blog',DOMAINS[6],kind='candidate'),
]

DEFAULTS = dict(enabled=True, daily_events=40, daily_input_tokens=200000, daily_output_tokens=40000,
                max_documents=100, reasoning_effort='high', model_enabled=True,
                feed_minutes=30, page_minutes=120, research_minutes=1440)
