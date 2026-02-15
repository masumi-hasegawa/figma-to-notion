import os
import re
import json
import requests
from PIL import Image, ImageDraw, ImageFilter
from io import BytesIO
from notion_client import Client
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# 環境変数から取得
FIGMA_TOKEN = os.environ.get('FIGMA_TOKEN')
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
NOTION_DATABASE_ID = os.environ.get('NOTION_DATABASE_ID')

# デザイン設定
DESIGN_CONFIG = {
    'border_width': 12,
    'border_color': (255, 255, 255, 255),
    'shadow_blur': 22,
    'shadow_spread': 0,
    'shadow_color': (66, 59, 23, int(255 * 0.15)),
    'shadow_offset': (22, 22),
    'corner_radius': 12,
    'canvas_width': 1000,
    'canvas_height': 600,
    'background_color': (0, 0, 0, 0),
    'max_img_width_ratio': 0.5,
    'max_img_height_ratio': 0.85,
    'max_screen_height': 812,
    'figma_scale': 3,
}

# GitHub Pages設定
GITHUB_USERNAME = "masumi-hasegawa"
GITHUB_REPO = "figma-to-notion"


# ============================================================
# Figma URL パース
# ============================================================

def parse_figma_url(url):
    """Figma URLからfile_keyとnode_idを抽出する

    対応フォーマット:
      https://www.figma.com/file/ABC123/FileName?node-id=1234-5678
      https://www.figma.com/design/ABC123/FileName?node-id=1234-5678
      https://www.figma.com/design/ABC123/FileName?node-id=1234%3A5678
    """
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')

    # /file/KEY/... or /design/KEY/...
    if len(path_parts) < 2:
        return None, None

    file_key = path_parts[1]

    # node-idをクエリパラメータから取得
    params = parse_qs(parsed.query)
    node_id = params.get('node-id', [None])[0]

    if node_id:
        # URLエンコードされた「:」を「-」に統一（Figma APIは「:」区切り）
        # ただしAPIに渡す際は元の形式で
        node_id = node_id.replace('-', ':')

    return file_key, node_id


# ============================================================
# Notion DB 読み書き
# ============================================================

def get_notion_pages():
    """Notion DBから全ページを取得（Figma URL付きのもの）"""
    notion = Client(auth=NOTION_TOKEN)

    pages = []
    has_more = True
    start_cursor = None

    while has_more:
        kwargs = {"database_id": NOTION_DATABASE_ID}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor

        response = notion.databases.query(**kwargs)

        for page in response['results']:
            props = page['properties']

            # 画面名を取得
            name_prop = props.get('画面名', {})
            name = ''
            if name_prop.get('title'):
                name = name_prop['title'][0]['text']['content']

            # Figma URLを取得
            figma_url_prop = props.get('Figma URL', {})
            figma_url = figma_url_prop.get('url', '')

            if name and figma_url:
                pages.append({
                    'page_id': page['id'],
                    'name': name,
                    'figma_url': figma_url,
                    'last_edited': page['last_edited_time'],
                })

        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    return pages


def update_notion_page_image(page_id, image_url):
    """既存のNotionページの画像ブロックを更新する"""
    notion = Client(auth=NOTION_TOKEN)

    # 既存の子ブロックを取得
    children = notion.blocks.children.list(block_id=page_id)

    # 既存の画像ブロックを削除
    for block in children['results']:
        if block['type'] == 'image':
            notion.blocks.delete(block_id=block['id'])

    # 新しい画像ブロックを追加
    notion.blocks.children.append(
        block_id=page_id,
        children=[
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {
                        "url": image_url
                    }
                }
            }
        ]
    )


# ============================================================
# Figma API
# ============================================================

def get_figma_last_modified(file_key):
    """Figmaファイルの最終更新日時を取得"""
    headers = {'X-Figma-Token': FIGMA_TOKEN}
    url = f"https://api.figma.com/v1/files/{file_key}?depth=1"
    response = requests.get(url, headers=headers)
    data = response.json()
    return data.get('lastModified', '')


def get_figma_image_url(file_key, node_id):
    """Figma APIから画像URLを取得"""
    headers = {'X-Figma-Token': FIGMA_TOKEN}
    scale = DESIGN_CONFIG['figma_scale']
    url = f"https://api.figma.com/v1/images/{file_key}?ids={node_id}&format=png&scale={scale}"
    response = requests.get(url, headers=headers)
    data = response.json()

    if 'images' in data and node_id in data['images']:
        return data['images'][node_id]
    return None


# ============================================================
# 画像加工（既存ロジック）
# ============================================================

def add_rounded_corners(img, radius):
    """画像に角丸を追加"""
    mask = Image.new('L', img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), img.size], radius=radius, fill=255)
    img = img.convert('RGBA')
    img.putalpha(mask)
    return img


def create_styled_image(figma_image_url):
    """Figma画像にスタイル（白枠+ドロップシャドウ）を適用"""

    # Figma画像を取得
    figma_response = requests.get(figma_image_url)
    figma_img = Image.open(BytesIO(figma_response.content)).convert('RGBA')

    # スマホ画面の高さ上限でcrop（3xスケール考慮: 812 * 3 = 2436px）
    max_height_px = DESIGN_CONFIG['max_screen_height'] * DESIGN_CONFIG['figma_scale']
    if figma_img.height > max_height_px:
        figma_img = figma_img.crop((0, 0, figma_img.width, max_height_px))

    # キャンバスサイズを取得
    canvas_width = DESIGN_CONFIG['canvas_width']
    canvas_height = DESIGN_CONFIG['canvas_height']

    # 画像をキャンバスに対して適切にリサイズ
    max_img_width = int(canvas_width * DESIGN_CONFIG['max_img_width_ratio'])
    max_img_height = int(canvas_height * DESIGN_CONFIG['max_img_height_ratio'])

    # アスペクト比を維持してリサイズ
    img_ratio = figma_img.width / figma_img.height
    target_ratio = max_img_width / max_img_height

    if img_ratio > target_ratio:
        new_width = max_img_width
        new_height = int(new_width / img_ratio)
    else:
        new_height = max_img_height
        new_width = int(new_height * img_ratio)

    figma_img = figma_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # 角丸を追加
    figma_rounded = add_rounded_corners(figma_img, DESIGN_CONFIG['corner_radius'])

    # 白枠を追加
    border_width = DESIGN_CONFIG['border_width']
    bordered_size = (
        figma_rounded.width + border_width * 2,
        figma_rounded.height + border_width * 2
    )
    bordered = Image.new('RGBA', bordered_size, (0, 0, 0, 0))
    bordered_draw = ImageDraw.Draw(bordered)
    bordered_draw.rounded_rectangle(
        [(0, 0), bordered_size],
        radius=DESIGN_CONFIG['corner_radius'],
        fill=DESIGN_CONFIG['border_color']
    )
    bordered.paste(figma_rounded, (border_width, border_width), figma_rounded)

    # 固定サイズのキャンバスを作成
    final = Image.new('RGBA', (canvas_width, canvas_height), DESIGN_CONFIG['background_color'])

    # 影を作成
    shadow_margin = DESIGN_CONFIG['shadow_blur'] + 50
    shadow_layer_size = (
        bordered.width + shadow_margin * 2,
        bordered.height + shadow_margin * 2
    )
    shadow = Image.new('RGBA', shadow_layer_size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)

    shadow_x = shadow_margin + DESIGN_CONFIG['shadow_offset'][0]
    shadow_y = shadow_margin + DESIGN_CONFIG['shadow_offset'][1]
    shadow_box = [shadow_x, shadow_y, shadow_x + bordered.width, shadow_y + bordered.height]
    shadow_draw.rounded_rectangle(
        shadow_box,
        radius=DESIGN_CONFIG['corner_radius'],
        fill=DESIGN_CONFIG['shadow_color']
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=DESIGN_CONFIG['shadow_blur']))

    # 影を配置
    shadow_x_on_canvas = (canvas_width - shadow.width) // 2
    shadow_y_on_canvas = (canvas_height - shadow.height) // 2
    final.paste(shadow, (shadow_x_on_canvas, shadow_y_on_canvas), shadow)

    # 白枠付き画像を配置
    bordered_x = (canvas_width - bordered.width) // 2
    bordered_y = (canvas_height - bordered.height) // 2
    final.paste(bordered, (bordered_x, bordered_y), bordered)

    # BytesIOに保存
    output = BytesIO()
    final.save(output, format='PNG')
    output.seek(0)
    return output


# ============================================================
# 画像保存 & GitHub Pages URL生成
# ============================================================

def save_image(name, styled_image):
    """画像をローカルに保存し、GitHub Pages URLを返す"""
    os.makedirs('images', exist_ok=True)

    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    safe_name = safe_name.replace(' ', '_')
    image_filename = f"{safe_name}.png"
    image_path = f"images/{image_filename}"

    with open(image_path, 'wb') as f:
        styled_image.seek(0)
        f.write(styled_image.read())

    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    image_url = f"https://{GITHUB_USERNAME}.github.io/{GITHUB_REPO}/{image_path}?v={timestamp}"

    return image_path, image_url


# ============================================================
# バージョン管理（Figma更新検知）
# ============================================================

VERSION_FILE = 'figma_versions.json'

def load_versions():
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_versions(versions):
    with open(VERSION_FILE, 'w') as f:
        json.dump(versions, f, indent=2)


# ============================================================
# メイン処理
# ============================================================

def main():
    print("Notion DBからページ一覧を取得中...")
    pages = get_notion_pages()

    if not pages:
        print("Figma URLが設定されたページがありません")
        return

    print(f"{len(pages)}件のページを取得しました\n")

    saved_versions = load_versions()
    updated_versions = {}

    # Figmaファイルごとの最終更新日時をキャッシュ
    figma_modified_cache = {}

    for i, page in enumerate(pages):
        name = page['name']
        figma_url = page['figma_url']
        page_id = page['page_id']

        print(f"処理中 ({i+1}/{len(pages)}): {name}")

        # Figma URLをパース
        file_key, node_id = parse_figma_url(figma_url)
        if not file_key or not node_id:
            print(f"  ⚠ Figma URLのパースに失敗: {figma_url}")
            continue

        # Figmaファイルの最終更新日時を取得（キャッシュ）
        if file_key not in figma_modified_cache:
            figma_modified_cache[file_key] = get_figma_last_modified(file_key)
        figma_last_modified = figma_modified_cache[file_key]

        # 更新チェック（node_idごとに管理）
        version_key = f"{file_key}/{node_id}"
        if version_key in saved_versions and saved_versions[version_key] == figma_last_modified:
            print(f"  スキップ (更新なし)")
            updated_versions[version_key] = figma_last_modified
            continue

        # Figmaから画像URLを取得
        image_url = get_figma_image_url(file_key, node_id)
        if not image_url:
            print(f"  ⚠ Figma画像の取得に失敗")
            continue

        # スタイルを適用
        styled = create_styled_image(image_url)

        # 画像を保存 & URL生成
        image_path, github_pages_url = save_image(name, styled)

        # Notionページの画像を更新
        update_notion_page_image(page_id, github_pages_url)

        updated_versions[version_key] = figma_last_modified
        print(f"  ✓ 更新完了: {github_pages_url}")

    # バージョン情報を保存
    save_versions(updated_versions)

    print(f"\n完了!")


if __name__ == "__main__":
    main()
