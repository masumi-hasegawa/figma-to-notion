import os
import requests
from PIL import Image
from io import BytesIO
from notion_client import Client

# 環境変数から取得
FIGMA_TOKEN = os.environ.get('FIGMA_TOKEN')
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
NOTION_DATABASE_ID = os.environ.get('NOTION_DATABASE_ID')
TEMPLATE_IMAGE_URL = os.environ.get('TEMPLATE_IMAGE_URL')
FIGMA_FILE_ID = os.environ.get('FIGMA_FILE_ID')
FIGMA_NODE_IDS = os.environ.get('FIGMA_NODE_IDS', '').split(',')

# スマホ画面の位置
PHONE_POSITION = {
    'x': 410,
    'y': 360,
    'width': 380,
    'height': 680
}

def get_figma_images():
    """Figmaから画像を取得"""
    headers = {'X-Figma-Token': FIGMA_TOKEN}
    
    images = []
    for node_id in FIGMA_NODE_IDS:
        if not node_id.strip():
            continue
            
        # 画像URLを取得
        url = f"https://api.figma.com/v1/images/{FIGMA_FILE_ID}?ids={node_id}&format=png&scale=2"
        response = requests.get(url, headers=headers)
        data = response.json()
        
        if 'images' in data and node_id in data['images']:
            image_url = data['images'][node_id]
            
            # ノード情報を取得（画面名など）
            node_url = f"https://api.figma.com/v1/files/{FIGMA_FILE_ID}/nodes?ids={node_id}"
            node_response = requests.get(node_url, headers=headers)
            node_data = node_response.json()
            
            name = node_data['nodes'][node_id]['document']['name'] if 'nodes' in node_data else f"Screen_{node_id}"
            
            images.append({
                'url': image_url,
                'name': name,
                'node_id': node_id
            })
    
    return images

def merge_image_with_template(figma_image_url):
    """Figma画像をテンプレートに合成"""
    # テンプレート画像を取得
    template_response = requests.get(TEMPLATE_IMAGE_URL)
    template = Image.open(BytesIO(template_response.content))
    
    # Figma画像を取得
    figma_response = requests.get(figma_image_url)
    figma_img = Image.open(BytesIO(figma_response.content))
    
    # リサイズ
    figma_resized = figma_img.resize(
        (PHONE_POSITION['width'], PHONE_POSITION['height']),
        Image.Resampling.LANCZOS
    )
    
    # 合成
    template.paste(figma_resized, (PHONE_POSITION['x'], PHONE_POSITION['y']))
    
    # BytesIOに保存
    output = BytesIO()
    template.save(output, format='PNG')
    output.seek(0)
    
    return output

def upload_to_notion(name, merged_image, figma_url):
    """Notionに登録"""
    notion = Client(auth=NOTION_TOKEN)
    
    # 画像をアップロード（一時的にどこかに保存する必要があるため、外部サービスを使うか、直接base64で埋め込む）
    # ここでは簡略化のため、Notion APIの制約上、外部URLが必要
    # 実際には画像をどこかにホストする必要があります
    
    # ページを作成
    notion.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties={
            "画面名": {"title": [{"text": {"content": name}}]},
            "Figma URL": {"url": figma_url}
        }
    )
    
    print(f"✓ {name} をNotionに登録しました")

def main():
    print("Figma画像を取得中...")
    figma_images = get_figma_images()
    
    print(f"{len(figma_images)}個の画像を取得しました")
    
    for img in figma_images:
        print(f"処理中: {img['name']}")
        
        # 画像を合成
        merged = merge_image_with_template(img['url'])
        
        # Notionに登録
        figma_url = f"https://www.figma.com/file/{FIGMA_FILE_ID}?node-id={img['node_id']}"
        upload_to_notion(img['name'], merged, figma_url)
    
    print("完了！")

if __name__ == "__main__":
    main()
