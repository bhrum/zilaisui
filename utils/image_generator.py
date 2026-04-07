import os
from PIL import Image, ImageDraw, ImageFont

def generate_cover_image(title: str, output_dir: str = "sucai/covers", filename: str = None) -> str:
    """
    Generate a 900x383 WeChat cover image with the given title centered.
    
    Args:
        title (str): The article title.
        output_dir (str): Directory where the image will be saved.
        filename (str): Optional custom filename. If None, uses a sanitized title.
        
    Returns:
        str: Absolute path to the generated image file.
    """
    # Create directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    if not filename:
        # Sanitize filename (keep only alphanumeric and basic Chinese characters/symbols approx)
        safe_title = "".join(c for c in title if c.isalnum() or '\u4e00' <= c <= '\u9fff' or c in 'M.卍() ')
        safe_title = safe_title.strip()[:30] # Limit length
        filename = f"cover_{safe_title}.png"
        
    output_path = os.path.abspath(os.path.join(output_dir, filename))
    
    # WeChat standard cover size is 900x383
    width, height = 900, 383
    
    # Background color (Dark gradient effect using a solid nice dark indigo)
    bg_color = (25, 30, 45)  # Dark blueish gray
    
    # Create image
    img = Image.new('RGB', (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    # Find a suitable font
    font_path = r"C:\Windows\Fonts\msyh.ttc"  # Windows Microsoft YaHei
    if not os.path.exists(font_path):
        # Fallback to general
        font_path = "arial.ttf" 
        
    # Dynamically scale font size based on title length
    # A base font size of 50 is good for about 15-20 characters
    font_size = 50
    if len(title) > 20:
        font_size = int(50 * (20 / len(title)))
        font_size = max(24, font_size) # minimum readable size
        
    try:
        font = ImageFont.truetype(font_path, font_size)
    except IOError:
        font = ImageFont.load_default()
        
    # Calculate text bounding box to center it
    try:
        # getbbox returns (left, top, right, bottom)
        bbox = draw.textbbox((0, 0), title, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except AttributeError:
        # For older PIL versions
        text_w, text_h = draw.textsize(title, font=font)
        
    # If text is somehow too wide, try shrinking font
    while text_w > width - 80 and font_size > 20:
        font_size -= 2
        font = ImageFont.truetype(font_path, font_size)
        try:
            bbox = draw.textbbox((0, 0), title, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except AttributeError:
            text_w, text_h = draw.textsize(title, font=font)
        
    # Calculate exact coordinates to place text in the center
    x = (width - text_w) / 2
    y = (height - text_h) / 2
    
    # Text Color (Off-white/light gray)
    text_color = (240, 240, 245)
    
    draw.text((x, y-10), title, font=font, fill=text_color)
    
    # Save the generated image
    img.save(output_path)
    return output_path

if __name__ == "__main__":
    # Test script locally
    test_title = "M01.01卍轮王佛顶一字陀罗尼卍"
    print(f"Generated test cover at: {generate_cover_image(test_title)}")
