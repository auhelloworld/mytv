#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AU TVBox Source Cleaner & Optimizer
专为澳洲本地网络环境打造的 TVBox / FongMi 影视源聚合与智能筛选器
支持本地运行与 GitHub Actions 自动云端定时更新
"""

import os
import re
import json
import time
import base64
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "public")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 澳洲环境坚决剔除的关键词黑名单
BLACKLIST_KEYWORDS = [
    # 磁力 / BT / P2P（规避澳洲版权法监控 & 寻种卡死）
    "磁力", "bt", "magnet", "torrent", "p2p", "荐片", "jianpian",
    # 网盘搜片（澳洲无国内SVIP限速/需扫码）
    "网盘", "云盘", "阿里", "夸克", "百度", "115", "天翼", "uc", "pan", "quark", 
    "alishare", "push_agent", "duopan", "misou", "pansearch", "mydrive", "clouddrive",
    # 非影视冗余项（教育、戏曲、儿童、预告片）
    "课堂", "教学", "少儿", "小学", "初中", "高中", "急救", "启蒙", "戏曲", "儿歌", "预告", "片单",
    # 杂项与引流广告
    "配置", "tg", "telegram", "弹幕", "本地文件", "qq频道", "说明", "公告", "防失联", "打赏", "贩卖"
]

UPSTREAM_SOURCES = [
    {
        "name": "肥猫",
        "url": "http://xn--z7x900a.net/",
        "type": "plain"
    },
    {
        "name": "饭太硬",
        "url": "http://www.xn--sss604efuw.net/tv",
        "type": "jpeg_base64"
    }
]

def get_base_url() -> str:
    """获取分发基准地址：在 GitHub Actions 环境下自动获取 Pages 域名，本地默认使用局域网 IP"""
    gh_repo = os.environ.get("GITHUB_REPOSITORY")
    if gh_repo and "/" in gh_repo:
        owner, repo_name = gh_repo.split("/")
        return f"https://{owner.lower()}.github.io/{repo_name}"
    
    # 环境变量或本地默认 Wi-Fi IP
    return os.environ.get("BASE_URL", "http://192.168.50.46:8080")

def clean_json_str(text: str) -> str:
    """清理 JSON 中的单行/多行注释和末尾多余逗号"""
    text = re.sub(r'^\s*//.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r',\s*([\]}])', r'\1', text)
    return text

def fetch_upstream(src: dict) -> dict:
    """获取并解码上游源"""
    name = src["name"]
    url = src["url"]
    print(f"[*] 正在拉取上游源: {name} ({url})...")
    headers = {"User-Agent": "okhttp/3.15.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        raw = r.content

        # 饭太硬等图片伪装源
        if src["type"] == "jpeg_base64" or raw.startswith(b"\xff\xd8"):
            idx = raw.find(b"\xff\xd9")
            if idx != -1:
                tail = raw[idx+2:]
                if b"**" in tail:
                    b64 = tail.split(b"**", 1)[1]
                else:
                    b64 = tail
                decoded = base64.b64decode(b64).decode("utf-8", errors="ignore")
                return json.loads(clean_json_str(decoded), strict=False)

        # 普通文本或 base64
        text = raw.decode("utf-8", errors="ignore").strip()
        if text.startswith("{"):
            return json.loads(clean_json_str(text), strict=False)
        else:
            try:
                dec = base64.b64decode(text).decode("utf-8", errors="ignore")
                return json.loads(clean_json_str(dec), strict=False)
            except Exception:
                return {}
    except Exception as e:
        print(f"[-] 上游源 {name} 拉取失败: {e}")
        return {}

def is_blacklisted(site: dict) -> bool:
    """检查站点是否命中黑名单（磁力、网盘、教育、垃圾引流）"""
    target = f"{site.get('key', '')} {site.get('name', '')} {site.get('api', '')}".lower()
    ext = site.get("ext", "")
    if isinstance(ext, str):
        target += f" {ext.lower()}"
    
    for kw in BLACKLIST_KEYWORDS:
        if kw in target:
            return True
    return False

def test_site_latency(site: dict) -> dict:
    """测试站点连通性与 HTTP 延迟 (ms)"""
    ext = site.get("ext", "")
    api = site.get("api", "")
    
    test_url = None
    if isinstance(ext, str) and ext.startswith("http"):
        test_url = ext
    elif isinstance(api, str) and api.startswith("http"):
        test_url = api

    latency = 120
    is_alive = True
    
    if test_url:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        start = time.time()
        try:
            r = requests.head(test_url, headers=headers, timeout=2.5, allow_redirects=True)
            if r.status_code in [200, 301, 302, 403]:
                latency = int((time.time() - start) * 1000)
            elif r.status_code in [404, 502, 503, 504]:
                is_alive = False
        except Exception:
            try:
                start = time.time()
                r = requests.get(test_url, headers=headers, timeout=2.5, stream=True)
                if r.status_code in [200, 301, 302, 403]:
                    latency = int((time.time() - start) * 1000)
                elif r.status_code in [404, 502, 503]:
                    is_alive = False
            except Exception:
                is_alive = False

    site_copy = dict(site)
    site_copy["_latency"] = latency
    site_copy["_is_alive"] = is_alive
    return site_copy

def main():
    base_url = get_base_url().rstrip("/")
    print("=" * 60)
    print("  🚀 开始执行 AU TVBox 影视源聚合与测速清洗...")
    print(f"  🌐 分发基准 Base URL: {base_url}")
    print("=" * 60)

    all_sites = []
    seen_keys = set()
    merged_rules = []
    seen_rule_names = set()
    merged_ads = set()
    master_spider = None
    master_wallpaper = "https://深色壁纸.xxooo.cf/"

    # 1. 抓取所有上游并合并
    for src in UPSTREAM_SOURCES:
        data = fetch_upstream(src)
        if not data:
            continue
        
        if not master_spider and data.get("spider"):
            master_spider = data.get("spider")

        # 收集去广告 rules
        for r in data.get("rules", []):
            rname = r.get("name", "")
            if rname and rname not in seen_rule_names:
                seen_rule_names.add(rname)
                merged_rules.append(r)

        # 收集广告黑名单
        for a in data.get("ads", []):
            merged_ads.add(a)

        # 过滤并收集站点
        sites = data.get("sites", [])
        for s in sites:
            key = s.get("key", "")
            if not key or key in seen_keys:
                continue
            if is_blacklisted(s):
                continue
            seen_keys.add(key)
            all_sites.append(s)

    print(f"\n[+] 过滤掉网盘/磁力/课堂/预告后，候选站点数: {len(all_sites)} 个")
    print("[*] 正在并发测速 (检测真实响应延迟)...")

    # 2. 并发测速
    tested_sites = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(test_site_latency, all_sites)
        for s in results:
            if s["_is_alive"] and s["_latency"] < 3000:
                tested_sites.append(s)

    tested_sites.sort(key=lambda x: x["_latency"])
    print(f"[+] 存活且延迟合格站点数: {len(tested_sites)} 个")

    core_keys = ["厂长", "csp_Jpys", "csp_Wwys", "csp_Gz360", "csp_SaoHuo", "csp_SP360", "立播", "光影", "海绵"]

    # 3. 构建 3 个不同特性的专属仓
    # --- 仓 1：极速主力仓 (8 个秒播直连大厂站，searchable=1) ---
    fast_sites = []
    for ck in core_keys:
        for s in tested_sites:
            if ck in s.get("key", "") or ck in s.get("name", ""):
                s_clean = {k: v for k, v in s.items() if not k.startswith("_")}
                s_clean["searchable"] = 1
                s_clean["quickSearch"] = 1
                raw_name = re.sub(r'^[^\w\s]+┃', '', s_clean["name"]).strip()
                s_clean["name"] = f"🚀┃{raw_name}┃[1080P/秒播]"
                if s_clean not in fast_sites:
                    fast_sites.append(s_clean)
                break
        if len(fast_sites) >= 8:
            break

    for s in tested_sites:
        if len(fast_sites) >= 8:
            break
        s_clean = {k: v for k, v in s.items() if not k.startswith("_")}
        s_clean["searchable"] = 1
        s_clean["quickSearch"] = 1
        raw_name = re.sub(r'^[^\w\s]+┃', '', s_clean["name"]).strip()
        s_clean["name"] = f"🚀┃{raw_name}┃[1080P/秒播]"
        if s_clean not in fast_sites:
            fast_sites.append(s_clean)

    # --- 仓 2：1080P/4K 高清画质精选仓 (精选画质站 + 动漫 + 体育) ---
    hd_sites = []
    hd_keys = core_keys + ["动漫", "看球", "体育", "哔哩", "糯米", "文采"]
    for hk in hd_keys:
        for s in tested_sites:
            if hk in s.get("key", "") or hk in s.get("name", ""):
                s_clean = {k: v for k, v in s.items() if not k.startswith("_")}
                s_clean["searchable"] = 1
                raw_name = re.sub(r'^[^\w\s]+┃', '', s_clean["name"]).strip()
                s_clean["name"] = f"🎬┃{raw_name}┃[超清纯净]"
                if s_clean not in hd_sites:
                    hd_sites.append(s_clean)
                break
        if len(hd_sites) >= 12:
            break

    # --- 仓 3：全网冷门兜底仓 (包含所有存活且无磁力/网盘的站点) ---
    backup_sites = []
    for s in tested_sites:
        s_clean = {k: v for k, v in s.items() if not k.startswith("_")}
        backup_sites.append(s_clean)

    # 4. 生成多仓 JSON
    def make_config(sites_list):
        return {
            "spider": master_spider,
            "wallpaper": master_wallpaper,
            "sites": sites_list,
            "rules": merged_rules,
            "ads": list(merged_ads)
        }

    with open(os.path.join(OUTPUT_DIR, "fast.json"), "w", encoding="utf-8") as f:
        json.dump(make_config(fast_sites), f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUTPUT_DIR, "hd.json"), "w", encoding="utf-8") as f:
        json.dump(make_config(hd_sites), f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUTPUT_DIR, "backup.json"), "w", encoding="utf-8") as f:
        json.dump(make_config(backup_sites), f, ensure_ascii=False, indent=2)

    # --- 多仓总索引配置 (multi.json) ---
    multi_config = {
        "urls": [
            {
                "name": "🚀 [澳洲极速主力仓 - 8大秒播站]",
                "url": f"{base_url}/fast.json"
            },
            {
                "name": "🎬 [1080P超清精选仓 - 含动漫/体育]",
                "url": f"{base_url}/hd.json"
            },
            {
                "name": "🛡️ [全网冷门兜底仓 - 片库最全]",
                "url": f"{base_url}/backup.json"
            }
        ]
    }
    with open(os.path.join(OUTPUT_DIR, "multi.json"), "w", encoding="utf-8") as f:
        json.dump(multi_config, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("  🎉 专属源生成完成！各仓站点统计:")
    print(f"  - 🚀 极速主力仓: {OUTPUT_DIR}/fast.json ({len(fast_sites)} 个站)")
    print(f"  - 🎬 高清精选仓: {OUTPUT_DIR}/hd.json ({len(hd_sites)} 个站)")
    print(f"  - 🛡️ 冷门兜底仓: {OUTPUT_DIR}/backup.json ({len(backup_sites)} 个站)")
    print(f"  - 🌐 多仓总索引: {OUTPUT_DIR}/multi.json (包含上述 3 仓)")
    print("=" * 60)

if __name__ == "__main__":
    main()
