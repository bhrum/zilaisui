"""
WeChat Official Account Backend Page Selectors
Centralized CSS/XPath selectors for mp.weixin.qq.com page elements.

NOTE: WeChat backend DOM may change over time. If automation breaks,
update the selectors here.
"""

# =============================================================================
# URLs
# =============================================================================
MP_LOGIN_URL = "https://mp.weixin.qq.com/"
MP_HOME_URL = "https://mp.weixin.qq.com/cgi-bin/home"
MP_NEW_ARTICLE_URL = (
    "https://mp.weixin.qq.com/cgi-bin/appmsg"
    "?t=media/appmsg_edit&action=edit&type=77&isNew=1&token={token}&lang=zh_CN"
)
# Fallback: create article via draft page
MP_DRAFT_LIST_URL = "https://mp.weixin.qq.com/cgi-bin/appmsg?begin=0&count=10&type=77&action=list_card&token={token}&lang=zh_CN"
MP_DRAFT_URL = "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77"

# =============================================================================
# Login Page Selectors
# =============================================================================
# QR code image for scanning
LOGIN_QR_CODE_SELECTOR = ".login__type__container__scan__qrcode img, .qrcode img"
# Login iframe (sometimes the QR code is inside an iframe)
LOGIN_IFRAME_SELECTOR = "iframe[src*='login']"
# Indicator that login is successful (user avatar or nickname element)
LOGIN_SUCCESS_INDICATOR = ".weui-desktop-account__nickname, .nickname, .main_bd"
# The "scan success, confirm on phone" status
LOGIN_SCAN_SUCCESS_SELECTOR = ".login__type__container__scan__status, .status_txt"

# =============================================================================
# Navigation / Home Page Selectors
# =============================================================================
# "New Article" button or menu entry
NEW_ARTICLE_BTN_SELECTOR = (
    "div.weui-desktop-card__inner:has-text('新的创作'), "
    "a:has-text('写新图文'), "
    "a:has-text('新建图文')"
)
# Account name display
ACCOUNT_NAME_SELECTOR = ".weui-desktop-account__nickname, .nickname, #nickname"

# =============================================================================
# Article Editor Selectors
# =============================================================================
# Title input
ARTICLE_TITLE_SELECTOR = "#title, input[name='title'], .title_input input"
# The editor iframe that contains the rich-text editing area
EDITOR_IFRAME_SELECTOR = "#ueditor_0, iframe.edui-editor-iframeholder"
# Editor body inside the iframe
EDITOR_BODY_SELECTOR = "body"
# Author input
ARTICLE_AUTHOR_SELECTOR = "#author, input[name='author'], .author_input input"
# Digest / summary textarea
ARTICLE_DIGEST_SELECTOR = (
    "#digest, textarea[name='digest'], "
    ".appmsg_digest textarea, .digest_area textarea"
)
# Cover image upload input (hidden file input — toolbar, NOT cover modal)
COVER_IMAGE_INPUT_SELECTOR = (
    "input[type='file'][name='file'], "
    "input[type='file'].upload__input, "
    ".cover_upload input[type='file']"
)
# Cover image area (click to trigger popup menu)
COVER_IMAGE_AREA_SELECTOR = (
    ".js_cover_btn_area, .select-cover__btn, .appmsg_cover, .cover_ct, "
    ".js_cover_area, .weui-desktop-form__input-area--cover"
)
# Original URL input
ORIGINAL_URL_SELECTOR = (
    "#content_source_url, input[name='content_source_url'], "
    ".source_url input"
)

# =============================================================================
# Cover Image Flow — Popup Menu (verified 2026-04-01)
# =============================================================================
# "从图片库选择" — opens Image Library modal (MUST use JS click, CSS=hidden)
COVER_POPUP_FROM_LIBRARY = "a.js_imagedialog"
# "从正文选择" — selects from article body images
COVER_POPUP_FROM_CONTENT = "a.js_selectCoverFromContent"
# "微信扫码上传"
COVER_POPUP_SCAN_UPLOAD = "a.js_imageScan"

# =============================================================================
# Image Library Modal (verified 2026-04-01)
# =============================================================================
# The visible modal: pick .weui-desktop-dialog where rect.width > 500
IMAGE_LIB_MODAL = ".weui-desktop-dialog"
IMAGE_LIB_TITLE = ".weui-desktop-dialog__title"
IMAGE_LIB_CLOSE_BTN = ".weui-desktop-dialog__close-btn"
# Image grid — thumbnails are <I> tags with background-image, NOT <img>!
IMAGE_LIB_GRID = ".weui-desktop-img-picker__list"
IMAGE_LIB_ITEM = ".weui-desktop-img-picker__item"
IMAGE_LIB_THUMB = "i.weui-desktop-img-picker__img-thumb"
# Upload button inside modal
IMAGE_LIB_UPLOAD_BTN = "button.single_upload_btn_container, button:has-text('上传文件')"
# Modal file input (index 1, no name — NOT the toolbar one at index 0)
IMAGE_LIB_FILE_INPUT = "input[type='file']:not([name='file'])"
# Next / cancel buttons
IMAGE_LIB_NEXT_BTN = "button.weui-desktop-btn_primary"
IMAGE_LIB_CANCEL_BTN = "button.weui-desktop-btn_default"
# Disabled state class
BTN_DISABLED_CLASS = "weui-desktop-btn_disabled"
# Category tabs
IMAGE_LIB_TAB = ".weui-desktop-menu__link"
IMAGE_LIB_TAB_ACTIVE = ".weui-desktop-menu__link_current"

# =============================================================================
# Crop Modal (after "下一步")
# =============================================================================
CROP_FINISH_BTN = (
    "button:has-text('确认'), "
    "button:has-text('完成'), "
    "button:has-text('确定')"
)

# =============================================================================
# Action Buttons
# =============================================================================
# Save as draft button
SAVE_DRAFT_BUTTON_SELECTOR = (
    "#js_send, "  # The main action button which defaults to draft save
    "a.weui-desktop-btn:has-text('保存为草稿'), "
    "button:has-text('保存为草稿'), "
    ".js_save_draft, "
    "a:has-text('存草稿')"
)
# Publish / Mass-send button
PUBLISH_BUTTON_SELECTOR = (
    "a:has-text('群发'), "
    "button:has-text('发布'), "
    "a:has-text('发布'), "
    ".js_send"
)
# Confirm dialog - OK/Confirm button
CONFIRM_DIALOG_OK_SELECTOR = (
    ".weui-desktop-btn_primary:has-text('确定'), "
    ".weui-desktop-dialog__btn-area .weui-desktop-btn_primary, "
    "button:has-text('确定'), "
    ".js_confirm"
)
# Success toast/notification
SUCCESS_TOAST_SELECTOR = (
    ".weui-desktop-toast, "
    ".success_tips, "
    ".tips_global_suc"
)
# Error message
ERROR_MESSAGE_SELECTOR = (
    ".weui-desktop-dialog__desc, "
    ".tips_global_err, "
    ".global_error"
)

# =============================================================================
# Token Extraction (from page URL or script)
# =============================================================================
TOKEN_URL_PATTERN = r"token=(\d+)"
TOKEN_SCRIPT_PATTERN = r"window\.__token\s*=\s*['\"]?(\d+)['\"]?"
