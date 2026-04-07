import os
import sys
import time
from playwright.sync_api import sync_playwright, TimeoutError

def get_elements_js():
    return """
    () => {
        function getCssPath(el) {
            if (!(el instanceof Element)) return;
            var path = [];
            while (el.nodeType === Node.ELEMENT_NODE && el.tagName.toLowerCase() !== 'html') {
                var selector = el.nodeName.toLowerCase();
                if (el.id) {
                    selector += '#' + el.id;
                    path.unshift(selector);
                    break;
                } else {
                    var sib = el, nth = 1;
                    while (sib = sib.previousElementSibling) {
                        if (sib.nodeName.toLowerCase() == selector)
                           nth++;
                    }
                    if (nth != 1) selector += ":nth-of-type("+nth+")";
                }
                path.unshift(selector);
                el = el.parentNode;
            }
            return path.join(" > ");
        }
        
        let interactables = Array.from(document.querySelectorAll('a, button, input, select, textarea, [role="button"], [tabindex]'));
        let results = [];
        interactables.forEach((el) => {
            let rect = el.getBoundingClientRect();
            let style = window.getComputedStyle(el);
            if (rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0') {
                let text = el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.title || '';
                text = text.trim().replace(/\\n/g, ' ').substring(0, 40);
                
                // Exclude elements that are just containers with too many children if they don't have distinct text
                if (el.tagName.toLowerCase() === 'div' && text === '' && el.children.length > 0) {
                    return;
                }
                
                results.push({
                    tag: el.tagName.toLowerCase(),
                    text: text,
                    selector: getCssPath(el),
                    type: el.type || ''
                });
            }
        });
        
        // Remove duplicates and elements with empty text that aren't inputs
        let uniqueResults = [];
        let seenSelectors = new Set();
        
        results.forEach(r => {
            if (!seenSelectors.has(r.selector)) {
                if (r.text === '' && !['input', 'textarea', 'select'].includes(r.tag)) {
                    // Skip empty non-inputs
                    return;
                }
                seenSelectors.add(r.selector);
                uniqueResults.push(r);
            }
        });
        
        return uniqueResults;
    }
    """

def main():
    print("=== Web Automation CLI Recorder ===")
    url = input("请输入要自动化的目标网址（带 http/https）: ").strip()
    if not url:
        print("网址不能为空。")
        return
    if not url.startswith('http'):
        url = 'https://' + url

    generated_lines = [
        "from playwright.sync_api import sync_playwright",
        "",
        "def run(playwright):",
        "    browser = playwright.chromium.launch(headless=False)",
        "    context = browser.new_context()",
        "    page = context.new_page()",
        f"    page.goto('{url}')"
    ]

    print("\n[+] 正在启动浏览器...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            page.goto(url)
            page.wait_for_load_state('networkidle', timeout=15000)
        except TimeoutError:
            print("[-] 页面加载超时（Network Idle），我们将继续。")
        except Exception as e:
            print(f"[-] 页面加载失败: {e}")
            return
            
        print("\n进入录制模式（你可以同时在打开的浏览器中预览页面变化）")
        
        step_count = 1
        while True:
            print("\n" + "="*50)
            print(">> 正在扫描页面可操作元素...")
            time.sleep(1) # wait for animations
            elements = page.evaluate(get_elements_js())
            
            if not elements:
                print("未找到任何互动元素。")
                print("你可以选择等待页面加载 [R] 刷新列表，或 [Q] 退出并生成脚本。")
            else:
                for idx, el in enumerate(elements):
                    tag = el['tag']
                    text = el['text']
                    ctype = f"({el['type']})" if el['type'] else ""
                    print(f"[{idx}] {tag.upper()}{ctype} | {text}")
                
            print("\n------------------------------")
            choice = input(f"输入元素编号进行交互，或输入 [R]重新扫描，[Q]完成退出生成脚本: ").strip()
            
            if choice.lower() == 'q':
                break
            elif choice.lower() == 'r':
                continue
            
            try:
                idx = int(choice)
                if idx < 0 or idx >= len(elements):
                    print("无效的编号！")
                    continue
            except ValueError:
                print("无效的输入！")
                continue
                
            sel_el = elements[idx]
            selector = sel_el['selector']
            tag = sel_el['tag']
            
            print(f"\n你选择了: <{tag}> {sel_el['text']}")
            print("请选择操作:")
            print("1. 点击 (Click)")
            print("2. 文本输入 (Fill/Type)")
            print("3. 回车键 (Press Enter)")
            print("4. 获取文本内容 (Wait & Get Text)")
            
            act_choice = input("请输入操作编号 (1-4, 回车默认点击, [C]取消): ").strip()
            if act_choice.lower() == 'c':
                continue
                
            if act_choice == '2':
                text_input = input("请输入你要填写的文本: ")
                print(f"[-] 正在执行输入: '{text_input}' ...")
                page.locator(selector).fill(text_input)
                generated_lines.append(f"    # Step {step_count}: Fill '{text_input}' into <{tag}> {sel_el['text']}")
                generated_lines.append(f"    page.locator('{selector}').fill('{text_input}')")
            elif act_choice == '3':
                print("[-] 正在按下回车键...")
                page.locator(selector).press('Enter')
                generated_lines.append(f"    # Step {step_count}: Press Enter on <{tag}> {sel_el['text']}")
                generated_lines.append(f"    page.locator('{selector}').press('Enter')")
            elif act_choice == '4':
                print("[-] 正在获取文本...")
                txt = page.locator(selector).inner_text()
                print(f"[+] 文本内容: {txt}")
                generated_lines.append(f"    # Step {step_count}: Get Text from <{tag}> {sel_el['text']}")
                generated_lines.append(f"    text_{step_count} = page.locator('{selector}').inner_text()")
                generated_lines.append(f"    print(text_{step_count})")
            else: # Defaults to 1
                try:
                    print("[-] 正在执行点击...")
                    page.locator(selector).click(timeout=5000)
                    generated_lines.append(f"    # Step {step_count}: Click <{tag}> {sel_el['text']}")
                    generated_lines.append(f"    page.locator('{selector}').click()")
                except TimeoutError:
                    print("[-] 点击超时，尝试强制跳过动画点击。")
                    page.locator(selector).click(force=True)
                    generated_lines.append(f"    # Step {step_count}: Click (Force) <{tag}> {sel_el['text']}")
                    generated_lines.append(f"    page.locator('{selector}').click(force=True)")

            # wait a bit after action
            page.wait_for_timeout(2000)
            step_count += 1
            
        print("\n\n=== 录制结束，正在生成脚本 ===")
        generated_lines.append("")
        generated_lines.append("    # Close browser (optional)")
        generated_lines.append("    # browser.close()")
        generated_lines.append("")
        generated_lines.append("if __name__ == '__main__':")
        generated_lines.append("    with sync_playwright() as playwright:")
        generated_lines.append("        run(playwright)")
        
        out_filename = "auto_script.py"
        with open(out_filename, "w", encoding="utf-8") as f:
            f.write("\n".join(generated_lines))
        
        print(f"[+] 脚本已生成保存为当前目录下的 {out_filename}！")
        print("\n".join(generated_lines))
        
if __name__ == "__main__":
    main()
