import os
import json
import requests
from PIL import Image, ImageDraw
from io import BytesIO
from notion_client import Client

# 環境変数から取得
FIGMA_TOKEN = os.environ.get('FIGMA_TOKEN')
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
NOTION_DATABASE_ID = os.environ.get('NOTION_DATABASE_ID')
TEMPLATE_IMAGE_URL = os.environ.get('TEMPLATE_IMAGE_URL')
FIGMA_FILE_ID = os.environ.get('FIGMA_FILE_ID')
FIGMA_NODE_IDS = os.environ.get('FIGMA_NODE_IDS', '').split(',')

# スマホ画面の位置と設定（調整済み）
PHONE_POSITION = {
    'x': 420,
    'y': 340,
    'width': 360,
    'height': 640,
    'angle': -2,  # 左に2度傾いている
    'corner_radius': 30  # 角丸の半径
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
        url = f"https://api.figma.com/v1/images/{FIGMA_FILE_ID}?ids={node_id}&format=png&scale=4"
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
    # 角丸マスクを作成
    mask = Image.new('L', img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), img.size], radius=radius, fill=255)
    
    # アルファチャンネルを追加
    img = img.convert('RGBA')
    img.putalpha(mask)
    
    return img

def merge_image_with_template(figma_image_url):
    """Figma画像をテンプレートに合成"""
    # テンプレート画像を取得
    template_response = requests.get(TEMPLATE_IMAGE_URL)
    template = Image.open(BytesIO(template_response.content)).convert('RGBA')
    
    # Figma画像を取得
    figma_response = requests.get(figma_image_url)
    figma_img = Image.open(BytesIO(figma_response.content)).convert('RGBA')
    
    # アスペクト比を維持してリサイズ
    # iPhoneの標準的なアスペクト比は約19.5:9
    target_width = PHONE_POSITION['width']
    target_height = PHONE_POSITION['height']
    
    # Figma画像のアスペクト比を計算
    figma_aspect = figma_img.width / figma_img.height
    target_aspect = target_width / target_height
    
    if figma_aspect > target_aspect:
        # 横が長い場合、幅を基準にリサイズ
        new_width = target_width
        new_height = int(target_width / figma_aspect)
    else:
        # 縦が長い場合、高さを基準にリサイズ
        new_height = target_height
        new_width = int(target_height * figma_aspect)
    
    figma_resized = figma_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # 角丸を追加
    figma_rounded = add_rounded_corners(figma_resized, PHONE_POSITION['corner_radius'])
    
    # 回転（角度がある場合のみ）
    if PHONE_POSITION['angle'] != 0:
        # 透明な背景で回転
        figma_rounded = figma_rounded.rotate(
            -PHONE_POSITION['angle'],  # PILは反時計回りなので符号を反転
            expand=True, 
            resample=Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0)
        )
    
    # 配置位置を計算（回転後のサイズを考慮）
    paste_x = PHONE_POSITION['x']
    paste_y = PHONE_POSITION['y']
    
    # 合成
    template.paste(figma_rounded, (paste_x, paste_y), figma_rounded)
    
    # BytesIOに保存
    output = BytesIO()
    template.save(output, format='PNG')
    output.seek(0)
    
    return output

def upload_to_notion(name, merged_image, figma_url):
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
        merged_image.seek(0)
        f.write(merged_image.read())
    
    # GitHub Pagesの公開URL
    github_username = "masumi-hasegawa"
    repo_name = "figma-to-notion"
    image_url = f"https://{github_username}.github.io/{repo_name}/{image_path}"
    
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
        
        # 画像を合成
        merged = merge_image_with_template(img['url'])
        
        # Notionに登録
        figma_url = f"https://www.figma.com/file/{FIGMA_FILE_ID}?node-id={img['node_id']}"
        upload_to_notion(img['name'], merged, figma_url)
        
        print()
    
    print("完了!")

if __name__ == "__main__":
    main()
