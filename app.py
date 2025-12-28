import streamlit as st
import edge_tts
import asyncio
import tempfile
import os
import pdfplumber
import re

# --- 1. 页面配置与 CSS 美化 ---
st.set_page_config(
    page_title="智能语音合成demo",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .stApp { background-color: #f8f9fa; }
    h1 { color: #2c3e50; font-family: 'Helvetica Neue', sans-serif; text-shadow: 2px 2px 4px #d1d1d1; }
    [data-testid="stSidebar"] { background-image: linear-gradient(#2e3b4e, #1c2331); color: white; }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3, [data-testid="stSidebar"] label, [data-testid="stSidebar"] .stMarkdown { color: #e0e0e0 !important; }
    .stButton>button { background: linear-gradient(45deg, #4b6cb7, #182848); color: white; border: none; border-radius: 8px; height: 50px; font-size: 18px; font-weight: bold; transition: all 0.3s ease; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    .stButton>button:hover { transform: translateY(-2px); box-shadow: 0 6px 12px rgba(0,0,0,0.2); }
    .stTextArea>div>div>textarea { border-radius: 10px; border: 1px solid #ddd; }
    .footer { position: fixed; left: 0; bottom: 0; width: 100%; background-color: #f1f1f1; color: #555; text-align: center; padding: 10px; font-size: 14px; border-top: 1px solid #ddd; z-index: 999; }
    .block-container { padding-bottom: 60px; }
</style>
""", unsafe_allow_html=True)

# --- 2. 智能文本处理模块 (核心修改部分) ---

class TextNormalizer:
    """处理文本中的数字、符号，使其符合特定语言的朗读习惯"""
    
    @staticmethod
    def is_english_dominant(text):
        """判断文本是否以英文为主"""
        # 移除空格和标点，只保留文字
        clean_text = re.sub(r'[^\w]', '', text)
        if not clean_text:
            return False
            
        # 统计英文字母数量
        en_count = len(re.findall(r'[a-zA-Z]', clean_text))
        # 统计中文字符数量 (Unicode 范围 4E00-9FFF)
        cn_count = len(re.findall(r'[\u4e00-\u9fff]', clean_text))
        
        # 如果英文字符数 > 中文字符数，认为是英文环境
        return en_count > cn_count

    @staticmethod
    def number_to_english(n):
        """简单的数字转英文单词函数 (支持 0-9999)"""
        try:
            n = int(n)
        except:
            return n # 如果不是数字，原样返回

        if n < 0 or n > 9999:
            return str(n) # 超出范围暂时原样返回

        ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]
        teens = ["Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
        tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

        def convert_hundred(num):
            if num < 10: return ones[num]
            elif num < 20: return teens[num-10]
            elif num < 100: return tens[num//10] + ((" " + ones[num%10]) if num%10 != 0 else "")
            else: return ones[num//100] + " Hundred" + ((" and " + convert_hundred(num%100)) if num%100 != 0 else "")

        if n == 0: return "Zero"
        if n < 1000: return convert_hundred(n)
        # 处理千位
        return convert_hundred(n // 1000) + " Thousand" + ((" " + convert_hundred(n % 1000)) if n % 1000 != 0 else "")

    @staticmethod
    def process(text):
        """主处理函数"""
        # 1. 判断语言环境
        if not TextNormalizer.is_english_dominant(text):
            # 如果是中文为主，直接返回，不强制修改，依赖引擎自身的中文处理
            return text
        
        # --- 以下是英文环境的处理逻辑 ---
        
        processed_text = text

        # 2. 处理货币：$50 -> fifty dollars
        # 正则匹配 $ 后面跟数字
        def replace_currency(match):
            number = match.group(1)
            word = TextNormalizer.number_to_english(number)
            return f"{word} dollars"
        
        processed_text = re.sub(r'\$(\d+)', replace_currency, processed_text)

        # 3. 处理特定标题：Part 1 -> Part One
        def replace_part(match):
            prefix = match.group(1) # "Part "
            number = match.group(2)
            word = TextNormalizer.number_to_english(number)
            return f"{prefix}{word}"
        
        processed_text = re.sub(r'(Part\s+)(\d+)', replace_part, processed_text, flags=re.IGNORECASE)

        # 4. (可选) 处理文中其他独立的数字：Tim has 2 apples -> Tim has two apples
        # 注意：这里使用 \b\d+\b 匹配单词边界的纯数字，避免破坏 dates (2023) 或 model numbers
        def replace_general_number(match):
            number = match.group(0)
            # 限制转换较小的数字，避免年份被读错 (例如只转换 0-100)
            if len(number) <= 2: 
                return TextNormalizer.number_to_english(number)
            return number
            
        processed_text = re.sub(r'\b\d+\b', replace_general_number, processed_text)

        return processed_text

# --- 3. 核心逻辑函数 ---

def extract_text_from_file(uploaded_file):
    if uploaded_file is None: return ""
    text = ""
    try:
        if uploaded_file.name.endswith('.pdf'):
            with pdfplumber.open(uploaded_file) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() + "\n"
        else:
            text = uploaded_file.read().decode("utf-8")
    except Exception as e:
        st.error(f"文件读取失败: {e}")
    return text

# 语音映射 (保持简化版：默认使用中文模型，因为中文模型支持中英混读)
VOICE_MAP = {
    "女": { "儿童": "zh-CN-XiaoyiNeural", "青年": "zh-CN-XiaoxiaoNeural", "中年": "zh-CN-Liaoning-XiaobeiNeural", "老年": "zh-HK-HiuGaaiNeural" },
    "男": { "儿童": "zh-CN-YunjianNeural", "青年": "zh-CN-YunxiNeural", "中年": "zh-CN-YunyangNeural", "老年": "zh-CN-YunyeNeural" }
}

def get_voice(gender, age):
    try: return VOICE_MAP[gender][age]
    except: return "zh-CN-XiaoxiaoNeural"

async def generate_audio_stream(text, voice, rate_str):
    communicate = edge_tts.Communicate(text, voice, rate=rate_str)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
        await communicate.save(tmp_file.name)
        return tmp_file.name

# --- 4. 界面布局 ---

with st.sidebar:
    st.header("🎛️ 语音合成参数")
    st.subheader("角色设定")
    gender = st.selectbox("👤 性别 (Gender)", ["女", "男"], index=0)
    age_group = st.selectbox("📅 年龄段 (Age Group)", ["儿童", "青年", "中年", "老年"], index=1)
    st.markdown("---")
    st.subheader("语速控制")
    speed_adjustment = st.slider("⏩ 语速调节", min_value=-50, max_value=50, value=0, step=5, help="负值变慢，正值变快。")
    st.markdown("---")
    st.info(f"💡 当前模型: **{get_voice(gender, age_group)}**")

st.title("🎙️ AI文本转语音生成器demo")
st.markdown("##### 自动识别中英文环境")

tab1, tab2 = st.tabs(["📝 文本输入", "📂 文件上传 (TXT/PDF)"])
input_text = ""

with tab1:
    default_text = """Part 1
Tim needs a new haircut. He goes to a hair salon.
Hairdresser: It’s $50.
Tim: Oh no! It’s terrible."""
    text_input_area = st.text_area("在此粘贴或输入文本:", height=250, value=default_text)
    if text_input_area: input_text = text_input_area

with tab2:
    uploaded_file = st.file_uploader("上传文件 (支持 .txt 或 .pdf)", type=['txt', 'pdf'])
    if uploaded_file:
        file_text = extract_text_from_file(uploaded_file)
        if file_text:
            st.success(f"✅ 成功读取文件，共 {len(file_text)} 个字符")
            with st.expander("查看文件内容预览"):
                st.text(file_text[:1000] + "..." if len(file_text) > 1000 else file_text)
            input_text = file_text

st.markdown("###")
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    generate_btn = st.button("🚀 开始生成语音 (Generate Audio)", use_container_width=True)

if generate_btn:
    if not input_text.strip():
        st.warning("⚠️ 请先输入文本或上传文件！")
    else:
        selected_voice = get_voice(gender, age_group)
        final_rate_str = f"{speed_adjustment:+d}%"
        
        # --- 智能预处理核心调用 ---
        is_en = TextNormalizer.is_english_dominant(input_text)
        status_msg = "检测到英文环境，正在优化数字与符号读音..." if is_en else "检测到中文环境，保持原样..."
        
        with st.spinner(f'🤖 {status_msg}'):
            # 1. 文本清洗与替换
            final_text = TextNormalizer.process(input_text)
            
            # (调试用：可以在后台打印处理后的文本)
            # print(f"Original: {input_text}\nProcessed: {final_text}")
            
            # 2. 调用 API
            try:
                mp3_path = asyncio.run(generate_audio_stream(final_text, selected_voice, final_rate_str))
                
                # 3. 展示结果
                st.success("✅ 生成完成！")
                st.markdown("---")
                st.subheader("🎧 试听与下载")
                audio_file = open(mp3_path, 'rb')
                audio_bytes = audio_file.read()
                st.audio(audio_bytes, format='audio/mp3')
                st.download_button(label="📥 下载 MP3 文件", data=audio_bytes, file_name="generated_audio.mp3", mime="audio/mp3")
            except Exception as e:
                st.error(f"❌ 生成错误: {e}")


st.markdown("<div class='footer'>华中师范大学沈威制作 &nbsp;&nbsp;|&nbsp;&nbsp; 邮箱：sw@ccnu.edu.cn</div>", unsafe_allow_html=True)
