"""Create a persistent browser profile for social-platform logins."""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


DEFAULT_PROFILE = Path(r"D:\news-aggregator-browser-profile")
PLATFORM_URLS = {
    "douyin": "https://www.douyin.com/",
    "bilibili": "https://www.bilibili.com/",
}
BROWSER_CHANNELS = {"edge": "msedge", "chromium": None}


def selected_platforms(platform):
    if platform == "all":
        return list(PLATFORM_URLS)
    return [platform]


def browser_channel(browser):
    return BROWSER_CHANNELS[browser]


def build_parser():
    parser = argparse.ArgumentParser(
        description="打开可见浏览器并保存抖音和 B 站的登录会话。"
    )
    parser.add_argument(
        "--platform",
        choices=(*PLATFORM_URLS, "all"),
        default="all",
        help="要登录的平台，默认为 all。",
    )
    parser.add_argument(
        "--browser",
        choices=tuple(BROWSER_CHANNELS),
        default="edge",
        help="用于登录的浏览器，默认为 Microsoft Edge。",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=DEFAULT_PROFILE,
        help=f"浏览器 Profile 目录，默认为 {DEFAULT_PROFILE}。",
    )
    return parser


def initialize_login(profile, platforms, channel=None):
    profile = profile.expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    print(f"登录会话将保存到：{profile}")
    print("请在打开的浏览器中自行完成登录；脚本不会读取或保存明文密码。")

    with sync_playwright() as playwright:
        try:
            launch_options = {
                "headless": False,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if channel:
                launch_options["channel"] = channel
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **launch_options,
            )
        except PlaywrightError as error:
            raise RuntimeError(
                "无法打开浏览器 Profile。请确认浏览器已安装，并关闭正在使用该目录的浏览器后重试。"
            ) from error

        try:
            pages = context.pages
            for index, platform in enumerate(platforms):
                page = pages[0] if index == 0 and pages else context.new_page()
                page.goto(PLATFORM_URLS[platform], wait_until="domcontentloaded", timeout=30000)
                print(f"已打开 {platform}：{PLATFORM_URLS[platform]}")
            input("完成所有登录后按 Enter 保存并关闭浏览器...")
        finally:
            context.close()


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        initialize_login(
            args.profile,
            selected_platforms(args.platform),
            browser_channel(args.browser),
        )
    except (OSError, RuntimeError, PlaywrightError) as error:
        print(f"登录初始化失败：{error}", file=sys.stderr)
        return 1
    print("登录会话已保存。现在可以运行社交来源拉取命令进行验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
