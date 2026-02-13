import os
import json
import requests
from PIL import Image, ImageDraw, ImageFilter
from io import BytesIO
from notion_client import Client
from datetime import datetime

# 環境変数から取得
FIGMA_TOKEN = os.environ.get('FIGMA_TOKEN')
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
NOTION_DATABASE_ID = os.environ.get('NOTION_DATABASE_ID')
FIGMA_FILE_ID = os.environ.get('FIGMA_FILE_ID')
FIGMA_NODE_IDS = os.environ.get('FIGMA_NODE_IDS', '').split(',')

# デザイン設定
DESIGN_CONFIG = {
    'border_width': 12,  # 外側白枠
    'border_color': (255, 255, 255, 255),  # 白
    'shadow_blur': 22,  # ぼかし22px
    'shadow_spread': 0,  # 広がり
    'shadow_color': (66, 59, 23, int(255 * 0.15)),  # #423B17, 15%
    'shadow_offset': (22, 22),  # 影の位置 X=22, Y=22
    'corner_radius': 12,  # 角丸
    'canvas_width': 1000,  # 最終画像の幅
    'canvas_height': 600,  # 最終画像の高さ
    'background_color': (0, 0, 0, 0),  # 背景色（透過）
    'max_img_width_ratio': 0.5,  # キャンバス幅に対する画像の最大幅（50%）
    'max_img_height_ratio': 0.85  # キャンバス高さに対する画像の最大高さ（85%）
}

# バージョン管理ファイル
VERSION_FILE = 'figma_versions.json'

def load_versions():
    """前回の更新日時を読み込み"""
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_versions(versions):
    """更新日時を保存"""
    with open(VERSION_FILE, 'w') as f:
        json.dump(versions, f, indent=2)

def get_figma_file_info():
    """Figmaファイル情報を取得（更新日時を含む）"""
    headers = {'X-Figma-Token': FIGMA_TOKEN}
    url = f"https://api.figma.com/v1/files/{FIGMA_FILE_ID}"
    response = requests.get(url, headers=headers)
    return response.json()

def get_figma_images():
    """Figmaから画像を取得（更新されたもののみ）"""
    headers = {'X-Figma-Token': FIGMA_TOKEN}
    
    # 前回のバージョン情報を読み込み
    saved_versions = load_versions()
    
    # ファイル情報を取得
    file_info = get_figma_file_info()
    last_modified = file_info.get('lastModified', '')
    
    images = []
    updated_versions = {}
    
    for node_id in FIGMA_NODE_IDS:
        if not node_id.strip():
            continue
            
        # ノード情報を取得
        node_url = f"https://api.figma.com/v1/files/{FIGMA_FILE_ID}/nodes?ids={node_id}"
        node_response = requests.get(node_url, headers=headers)
        node_data = node_response.json()
        
        if 'nodes' not in node_data or node_id not in node_data['nodes']:
            continue
        
        node_info = node_data['nodes'][node_id]
        name = node_info['document']['name'] if 'document' in node_info else f"Screen_{node_id}"
        
        # 更新チェック
        if node_id in saved_versions and saved_versions[node_id] == last_modified:
            print(f"スキップ: {name} (更新なし)")
            updated_versions[node_id] = last_modified
            continue
        
        # 画像URLを取得（高解像度）
        url = f"https://api.figma.com/v1/images/{FIGMA_FILE_ID}?ids={node_id}&format=png&scale=3"
        response = requests.get(url, headers=headers)
        data = response.json()
        
        if 'images' in data and node_id in data['images']:
            image_url = data['images'][node_id]
            
            images.append({
                'url': image_url,
                'name': name,
                'node_id': node_id
            })
            
            updated_versions[node_id] = last_modified
    
    # バージョン情報を保存
    save_versions(updated_versions)
    
    return images

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
        # 横長の場合、幅を基準に
        new_width = max_img_width
        new_height = int(new_width / img_ratio)
    else:
        # 縦長の場合、高さを基準に
        new_height = max_img_height
        new_width = int(new_height * img_ratio)
    
    figma_img = figma_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # 角丸を追加（スクリーンショット）
    figma_rounded = add_rounded_corners(figma_img, DESIGN_CONFIG['corner_radius'])
    
    # 白枠を追加（角丸あり）
    border_width = DESIGN_CONFIG['border_width']
    bordered_size = (
        figma_rounded.width + border_width * 2,
        figma_rounded.height + border_width * 2
    )
    
    # 白枠用の角丸画像を作成
    bordered = Image.new('RGBA', bordered_size, (0, 0, 0, 0))
    bordered_draw = ImageDraw.Draw(bordered)
    bordered_draw.rounded_rectangle(
        [(0, 0), bordered_size],
        radius=DESIGN_CONFIG['corner_radius'],
        fill=DESIGN_CONFIG['border_color']
    )
    # スクリーンショットを中央に配置
    bordered.paste(figma_rounded, (border_width, border_width), figma_rounded)
    
    # 固定サイズのキャンバスを作成（1000x600px、透過背景）
    final = Image.new('RGBA', (canvas_width, canvas_height), DESIGN_CONFIG['background_color'])
    
    # 影用のレイヤーを作成
    shadow_margin = DESIGN_CONFIG['shadow_blur'] + 50
    shadow_layer_size = (
        bordered.width + shadow_margin * 2,
        bordered.height + shadow_margin * 2
    )
    shadow = Image.new('RGBA', shadow_layer_size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    
    # 影の位置とサイズを計算
    shadow_x = shadow_margin + DESIGN_CONFIG['shadow_offset'][0]
    shadow_y = shadow_margin + DESIGN_CONFIG['shadow_offset'][1]
    shadow_box = [
        shadow_x,
        shadow_y,
        shadow_x + bordered.width,
        shadow_y + bordered.height
    ]
    
    # 影を描画（角丸）
    shadow_draw.rounded_rectangle(
        shadow_box,
        radius=DESIGN_CONFIG['corner_radius'],
        fill=DESIGN_CONFIG['shadow_color']
    )
    
    # 影をぼかす
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=DESIGN_CONFIG['shadow_blur']))
    
    # 影をキャンバス中央に配置
    shadow_x_on_canvas = (canvas_width - shadow.width) // 2
    shadow_y_on_canvas = (canvas_height - shadow.height) // 2
    final.paste(shadow, (shadow_x_on_canvas, shadow_y_on_canvas), shadow)
    
    # 白枠付き画像をキャンバス中央に配置
    bordered_x = (canvas_width - bordered.width) // 2
    bordered_y = (canvas_height - bordered.height) // 2
    final.paste(bordered, (bordered_x, bordered_y), bordered)
    
    # BytesIOに保存
    output = BytesIO()
    final.save(output, format='PNG')
    output.seek(0)
    
    return output

def upload_to_notion(name, styled_image, figma_url):
    """Notionに登録"""
    import base64
    
    # imagesディレクトリを作成
    os.makedirs('images', exist_ok=True)
    
    # 画像を保存
    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    safe_name = safe_name.replace(' ', '_')
    image_filename = f"{safe_name}.png"
    image_path = f"images/{image_filename}"
    
    # 保存
    with open(image_path, 'wb') as f:
        styled_image.seek(0)
        f.write(styled_image.read())
    
    # GitHub Pagesの公開URL（タイムスタンプ付き）
    github_username = "masumi-hasegawa"
    repo_name = "figma-to-notion"
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    image_url = f"https://{github_username}.github.io/{repo_name}/{image_path}?v={timestamp}"
    
    # Notionに登録
    notion = Client(auth=NOTION_TOKEN)
    
    page = notion.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties={
            "画面名": {"title": [{"text": {"content": name}}]},
            "Figma URL": {"url": figma_url}
        }
    )
    
    # ページに画像ブロックを追加
    notion.blocks.children.append(
        block_id=page['id'],
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
    
    print(f"✓ {name} をNotionに登録しました")
    print(f"  画像URL: {image_url}")

def main():
    print("Figmaファイルの更新をチェック中...")
    figma_images = get_figma_images()
    
    if not figma_images:
        print("更新された画像はありません")
        return
    
    print(f"{len(figma_images)}個の更新された画像を取得しました\n")
    
    for i, img in enumerate(figma_images):
        print(f"処理中 ({i+1}/{len(figma_images)}): {img['name']}")
        
        # スタイルを適用
        styled = create_styled_image(img['url'])
        
        # Notionに登録
        figma_url = f"https://www.figma.com/file/{FIGMA_FILE_ID}?node-id={img['node_id']}"
        upload_to_notion(img['name'], styled, figma_url)
        
        print()
    
    print("完了!")

if __name__ == "__main__":
    main()
