import base64
import re
from pathlib import Path

def inline_avatar():
    avatar_path = Path("app/static/avatar.png")
    html_path = Path("app/static/index.html")
    
    if not avatar_path.exists() or not html_path.exists():
        print("Missing avatar.png or index.html")
        return
        
    b64_data = base64.b64encode(avatar_path.read_bytes()).decode("utf-8")
    data_uri = f"data:image/png;base64,{b64_data}"
    
    html = html_path.read_text(encoding="utf-8")
    # Replace <div class="logo-badge">🎁</div> with <img src="..." class="app-logo-img">
    updated_html = re.sub(
        r'<div class="logo-badge">🎁</div>',
        f'<img src="{data_uri}" class="app-logo-img" alt="Tipa Logo">',
        html
    )
    
    html_path.write_text(updated_html, encoding="utf-8")
    print("Successfully replaced gift emoji with inlined 3D avatar logo in index.html!")

if __name__ == "__main__":
    inline_avatar()
