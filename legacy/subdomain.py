import asyncio
import aiohttp
import logging
from lxml import etree
from urllib.parse import urlparse, urljoin

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class AdvancedSubdomainCrawler:
    def __init__(self, root_domain, max_pages_per_domain=10):
        self.root_domain = root_domain
        self.visited_urls = set()
        self.found_subdomains = set()
        self.queue = asyncio.Queue()
        self.max_pages_per_domain = max_pages_per_domain
        # 记录每个子域名已爬取的页面数,防止在某个诡异子域名卡死，很重要！！！我真服了
        self.domain_visit_counts = {} 

    async def query_crt_sh(self, session):
        #crt.sh查询，别问我，我其实不知道这玩意怎么work，总之可以查证书然后全找到
        logging.info(f"✅正在查询 CT Logs (crt.sh) 获取历史子域名...")
        url = f"https://crt.sh/?q=%.{self.root_domain}&output=json"
        try:
            async with session.get(url, timeout=20) as response:
                if response.status == 200:
                    data = await response.json()
                    count = 0
                    for entry in data:
                        name_value = entry['name_value']
                        # crt.sh 有时返回多行域名，需分割
                        subdomains = name_value.split('\n')
                        for sub in subdomains:
                            # 清洗返回的值
                            sub = sub.replace('*.', '')
                            if sub not in self.found_subdomains and sub.endswith(self.root_domain):
                                self.found_subdomains.add(sub)
                                # 将发现的新子域名的首页，加入爬取队列
                                await self.queue.put(f"https://{sub}")
                                count += 1
                    logging.info(f"✅ CT Logs 查询完成，预注入 {count} 个子域名到队列！")
        except Exception as e:
            logging.warning(f"❌ CT Logs 查询失败: {e}")

    async def run(self):
        async with aiohttp.ClientSession() as session:
            # 扫描
            await self.query_crt_sh(session)
            
            # 确保主域名也在队列里
            if self.root_domain not in self.found_subdomains:
                await self.queue.put(f"https://www.{self.root_domain}")

            # 爬虫开始工作 
            logging.info("✅ 开始爬取验证")
            
            # 5个并发 worker 来处理队列
            tasks = []
            for i in range(5): 
                task = asyncio.create_task(self.worker(session, i))
                tasks.append(task)
            
            # 等待队列被清空
            await self.queue.join()
            
            # 取消所有任务
            for task in tasks:
                task.cancel()
                
        self.print_report()

    async def worker(self, session, worker_id):
        while True:
            try:
                # 从队列获取 URL，如果队列空了会在这里等待
                url = await self.queue.get()
                
                # 检查防卡死，太久就跳出！！！！！！！！！！！！！！！！！！！
                domain = urlparse(url).netloc
                current_count = self.domain_visit_counts.get(domain, 0)
                
                if url in self.visited_urls or current_count >= self.max_pages_per_domain:
                    self.queue.task_done()
                    continue
                
                self.visited_urls.add(url)
                self.domain_visit_counts[domain] = current_count + 1
                
                await self.fetch_and_parse(session, url)
                self.queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                # logging.error(f"Worker {worker_id} error: {e}")
                self.queue.task_done()

    async def fetch_and_parse(self, session, url):
        try:
            async with session.get(url, timeout=5) as response:
                # 只有200OK才认为是有效子域名并继续爬
                if response.status == 200:
                    # 记录这个域名是活的，防止尸体域名noise
                    domain = urlparse(url).netloc
                    self.found_subdomains.add(domain)
                    logging.info(f"✅ [200 OK] {url}")

                    # 解析HTML寻找更多链接
                    html = await response.text()
                    tree = etree.HTML(html)
                    if tree is None: return
                    
                    links = tree.xpath('//a/@href')
                    for link in links:
                        full_url = urljoin(url, link)
                        parsed = urlparse(full_url)
                        
                        # 只有是同一个根域名的，才加入队列
                        if parsed.netloc.endswith(self.root_domain):
                            # 防止重复 && 排除静态文件
                            if full_url not in self.visited_urls and not full_url.endswith(('.jpg', '.png', '.css', '.js')):
                                self.queue.put_nowait(full_url)
        except:
            # 访问失败（比如域名解析错误），说明这个子域名可能是死链，忽略
            print("❌ 访问失败，说明这个子域名可能是死链，忽略")

    def print_report(self):
        print("\n" + "="*40)
        print(f"✅ 最终结果报告: {self.root_domain}")
        print(f"✅ 扫描链接总数: {len(self.visited_urls)}")
        print(f"✅ 发现有效子域名: {len(self.found_subdomains)}")
        print("="*40)
        for sub in sorted(self.found_subdomains):
            print(sub)

if __name__ == "__main__":
    target = "illinois.edu"
    crawler = AdvancedSubdomainCrawler(target)
    asyncio.run(crawler.run())