import os
import json
import requests
import base64
import time
from io import BytesIO
from PIL import Image
from notion_client import Client
import google.generativeai as genai

# 環境変数から取得
FIGMA_TOKEN = os.environ.get('FIGMA_TOKEN')
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
NOTION_DATABASE_ID = os.environ.get('NOTION_DATABASE_ID')
FIGMA_FILE_ID = os.environ.get('FIGMA_FILE_ID')
FIGMA_NODE_IDS = os.environ.get('FIGMA_NODE_IDS', '').split(',')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

# Gemini API設定
genai.configure(api_key=GOOGLE_API_KEY)

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
        
        # 更新チェック（簡易版: ファイル全体の更新日時を使用）
        if node_id in saved_versions and saved_versions[node_id] == last_modified:
            print(f"スキップ: {name} (更新なし)")
            updated_versions[node_id] = last_modified
            continue
        
        # 画像URLを取得
        image_url_api = f"https://api.figma.com/v1/images/{FIGMA_FILE_ID}?ids={node_id}&format=png&scale=2"
        response = requests.get(image_url_api, headers=headers)
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

def load_prompts():
    """プロンプトバリエーションを読み込み"""
    with open('prompts.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def generate_scene_with_gemini(figma_image_url, prompt_text, screen_name):
    """Gemini APIで自然な利用シーン画像を生成"""
    
    # Figma画像を取得
    response = requests.get(figma_image_url)
    figma_image = Image.open(BytesIO(response.content))
    
    # 画像を一時保存
    temp_path = f"temp_{screen_name}.png"
    figma_image.save(temp_path)
    
    try:
        # Gemini 2.0 Flashモデルを使用
        model = genai.GenerativeModel('gemini-2.0-flash-exp')
        
        # 画像をアップロード
        uploaded_file = genai.upload_file(temp_path)
        
        # 完全なプロンプト
        full_prompt = f"""{prompt_text}

このスマホ画面の画像を、実際の日常生活シーンに自然に配置した写真を生成してください。
スマホ画面には提供された画像をそのまま使用し、周囲の環境（手、背景）のみを生成してください。

重要:
- スマホ画面の内容は変更しないでください
- 手の位置は自然な操作姿勢にしてください
- 背景は指定されたシーンに合わせてください
- リアルで生活感のある写真にしてください
"""
        
        print(f"  Gemini APIで画像生成中...")
        
        # 画像生成
        result = model.generate_content([full_prompt, uploaded_file])
        
        # 生成された画像を保存
        # 注: Gemini 2.0 Flashの画像生成レスポンス形式を確認
        if hasattr(result, 'parts') and result.parts:
            for part in result.parts:
                if hasattr(part, 'inline_data'):
                    image_data = part.inline_data.data
                    return base64.standard_b64encode(image_data).decode('utf-8')
        
        # 画像生成が失敗した場合、元のFigma画像を使用
        print(f"  警告: 画像生成に失敗、元の画像を使用します")
        with open(temp_path, 'rb') as f:
            return base64.standard_b64encode(f.read()).decode('utf-8')
            
    finally:
        # 一時ファイルを削除
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        # アップロードしたファイルを削除
        try:
            genai.delete_file(uploaded_file.name)
        except:
            pass

def upload_to_notion(name, image_data, figma_url):
    """Notionに登録"""
    # imagesディレクトリを作成
    os.makedirs('images', exist_ok=True)
    
    # 画像を保存
    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    safe_name = safe_name.replace(' ', '_')
    image_filename = f"{safe_name}.png"
    image_path = f"images/{image_filename}"
    
    # Base64デコードして保存
    with open(image_path, 'wb') as f:
        f.write(base64.standard_b64decode(image_data))
    
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
    
    # プロンプトを読み込み
    prompts_data = load_prompts()
    variations = prompts_data['variations']
    
    for i, img in enumerate(figma_images):
        print(f"処理中 ({i+1}/{len(figma_images)}): {img['name']}")
        
        # プロンプトをローテーション
        variation = variations[i % len(variations)]
        print(f"  バリエーション: {variation['name']}")
        
        # Gemini APIで画像生成
        generated_image = generate_scene_with_gemini(
            img['url'], 
            variation['prompt'],
            img['name']
        )
        
        # Notionに登録
        figma_url = f"https://www.figma.com/file/{FIGMA_FILE_ID}?node-id={img['node_id']}"
        upload_to_notion(img['name'], generated_image, figma_url)
        
        # レート制限対策
        if i < len(figma_images) - 1:
            print("  待機中...")
            time.sleep(5)
        
        print()
    
    print("完了!")

if __name__ == "__main__":
    main()
