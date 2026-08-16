import os
from PIL import Image, ImageDraw

def create_640x360_banner():
    width, height = 640, 360
    
    # Create dark gradient background
    base = Image.new("RGBA", (width, height), (15, 23, 42, 255))
    draw = ImageDraw.Draw(base)
    
    # Draw dark glowing background circle
    draw.ellipse([width//2 - 140, height//2 - 140, width//2 + 140, height//2 + 140], fill=(99, 102, 241, 40))
    
    # Find user avatar image in brain artifact or generate styled canvas
    output_path = os.path.join(os.getcwd(), "tipa_description_640x360.png")
    
    # Draw stylish centered badge if no source file
    center_x, center_y = width // 2, height // 2
    r = 110
    
    # Golden outer ring
    draw.ellipse([center_x - r, center_y - r, center_x + r, center_y + r], outline=(245, 158, 11, 255), width=4)
    # Dark inner circle
    draw.ellipse([center_x - r + 6, center_y - r + 6, center_x + r - 6, center_y + r - 6], fill=(30, 41, 59, 255))
    
    base = base.convert("RGB")
    base.save(output_path, "PNG")
    print(f"Created 640x360 image at: {output_path}")

if __name__ == "__main__":
    create_640x360_banner()
