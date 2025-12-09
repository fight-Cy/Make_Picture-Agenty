import streamlit as st
import os
import logging
from dotenv import load_dotenv
from dashscope import Generation, ImageSynthesis
from dashscope.api_entities.dashscope_response import Role
from langchain.memory import ConversationBufferMemory
from langchain.schema import HumanMessage, AIMessage
from http import HTTPStatus
from urllib.parse import urlparse, unquote
from pathlib import PurePosixPath
import requests

# 配置终端日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("AI绘图助手")

# 加载环境变量
load_dotenv()
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not DASHSCOPE_API_KEY:
    st.error("❌ 请在.env文件中配置DASHSCOPE_API_KEY")
    st.stop()

# 配置地域
ImageSynthesis.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
Generation.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'


# ---------------------- 用session_state持久化记忆模块 ----------------------
def init_memory():
    """初始化记忆模块，存储在session_state中（跨交互保留）"""
    if "conv_memory" not in st.session_state:
        memory = ConversationBufferMemory(
            memory_key="chat_history",
            return_messages=True
        )
        st.session_state.conv_memory = memory
    return st.session_state.conv_memory


class ConversationMemory:
    def __init__(self, memory):
        self.memory = memory
    
    def get_history(self):
        """获取历史对话，返回[(user_input, ai_output), ...]"""
        history = self.memory.load_memory_variables({})["chat_history"]
        history_pairs = []
        for i in range(0, len(history), 2):
            if i+1 < len(history):
                user_msg = history[i].content if isinstance(history[i], HumanMessage) else ""
                ai_msg = history[i+1].content if isinstance(history[i+1], AIMessage) else ""
                history_pairs.append((user_msg, ai_msg))
        logger.info(f"当前历史对话：{history_pairs}")
        return history_pairs
    
    def add_history(self, user_input, optimized_prompt):
        """添加新对话到记忆"""
        self.memory.save_context(
            {"input": user_input},
            {"output": optimized_prompt}
        )
        logger.info(f"新增对话 - 用户：{user_input} | 优化后Prompt：{optimized_prompt}")


# ---------------------- 通义千问Prompt优化（正确加载历史对话） ----------------------
def qwen_optimize_prompt(user_input, process_container):
    # 从session_state中获取记忆模块（跨交互保留）
    memory = init_memory()
    conv_memory = ConversationMemory(memory)
    
    process_container.write("🔍 开始解析用户需求...")
    logger.info(f"收到用户需求：{user_input}")
    
    system_prompt = """
你是资深AI绘图提示词工程师，专注于生成适配qwen-image-plus模型的高质量中文提示词。
要求：1. 包含主体+场景+细节（如材质、光影）；2. 多轮修改时保留原主体；3. 仅返回提示词，无额外解释。
    """
    messages = [{"role": Role.SYSTEM, "content": system_prompt}]
    
    # 加载历史对话
    history_pairs = conv_memory.get_history()
    if history_pairs:
        process_container.write("📝 加载历史对话，准备多轮修改...")
        logger.info("已加载历史对话，进入多轮优化模式")
        for user_msg, ai_msg in history_pairs:
            messages.append({"role": Role.USER, "content": user_msg})
            messages.append({"role": Role.ASSISTANT, "content": ai_msg})
    else:
        process_container.write("🆕 无历史对话，准备首次Prompt生成...")
        logger.info("无历史对话，进入首次优化模式")
    
    messages.append({"role": Role.USER, "content": user_input})
    
    process_container.write("🔄 调用通义千问生成优化Prompt...")
    logger.info("开始调用通义千问（qwen-turbo）优化Prompt")
    
    try:
        response = Generation.call(
            model="qwen-turbo",
            api_key=DASHSCOPE_API_KEY,
            messages=messages,
            temperature=0.7,
            result_format="message"
        )
        optimized_prompt = response.output.choices[0].message.content.strip()
        
        process_container.write(f"✅ Prompt优化完成：{optimized_prompt}")
        logger.info(f"Prompt优化成功，结果：{optimized_prompt}")
        
        conv_memory.add_history(user_input, optimized_prompt)
        return optimized_prompt
    except Exception as e:
        error_msg = f"❌ 通义千问优化失败：{str(e)}"
        process_container.error(error_msg)
        logger.error(error_msg)
        raise Exception(error_msg)


# 绘图API调用
def call_qwen_image_plus(optimized_prompt, process_container):
    process_container.write("🖌️ 调用qwen-image-plus模型生成图片...")
    logger.info(f"开始调用绘图模型，使用Prompt：{optimized_prompt}")
    
    try:
        rsp = ImageSynthesis.call(
            api_key=DASHSCOPE_API_KEY,
            model="qwen-image-plus",
            prompt=optimized_prompt,
            n=1,
            size='1328*1328',
            prompt_extend=True,
            watermark=True
        )
        
        process_container.write("📊 解析模型响应，提取图片URL...")
        if rsp.status_code == HTTPStatus.OK:
            if len(rsp.output.results) > 0:
                result = rsp.output.results[0]
                image_url = result.url
                process_container.write("🎉 图片生成成功！正在展示结果...")
                logger.info(f"图片生成成功，URL：{image_url}")
                return image_url
            else:
                raise Exception("模型响应中无图片结果")
        else:
            error_msg = f"模型调用失败：状态码{rsp.status_code}，错误{rsp.code}：{rsp.message}"
            process_container.error(error_msg)
            logger.error(error_msg)
            raise Exception(error_msg)
    except Exception as e:
        error_msg = f"❌ 图片生成失败：{str(e)}"
        process_container.error(error_msg)
        logger.error(error_msg)
        raise Exception(error_msg)


# Streamlit界面
st.title("AI 画图助手（通义千问+qwen-image-plus 官方版）")
st.subheader("📋 完整流程展示：输入→优化→生成")
st.caption("支持多轮交互：修改指令会基于历史Prompt调整（例：先输“赛博朋克风格猫”，再输“给这个猫带个墨镜”）")

process_container = st.container(border=True)
process_container.markdown("### 📝 系统操作日志")
process_container.write("等待用户输入需求...")

history_container = st.container(border=True)
history_container.markdown("### 🕒 多轮对话历史")

user_input = st.text_input("请输入绘图需求（例：画一只赛博朋克风格猫）：")

if st.button("生成图片") and user_input.strip():
    process_container.empty()
    process_container.markdown("### 📝 系统操作日志")
    
    try:
        optimized_prompt = qwen_optimize_prompt(user_input, process_container)
        
        st.subheader("✨ 优化后的最终Prompt")
        st.code(optimized_prompt, language="text")
        
        image_url = call_qwen_image_plus(optimized_prompt, process_container)
        
        st.subheader("🎨 图片生成结果")
        st.image(image_url, caption="qwen-image-plus生成图", use_column_width=True)
        st.download_button(
            label="下载图片",
            data=requests.get(image_url).content,
            file_name="qwen_image_plus.png",
            mime="image/png"
        )
        
        # 更新历史对话展示区
        history_container.empty()
        history_container.markdown("### 🕒 多轮对话历史")
        # 从session_state中获取最新历史对话
        memory = init_memory()
        conv_memory = ConversationMemory(memory)
        history_pairs = conv_memory.get_history()
        for idx, (user_msg, ai_msg) in enumerate(history_pairs, 1):
            history_container.markdown(f"**用户 {idx}：** {user_msg}")
            history_container.markdown(f"**系统优化 {idx}：** {ai_msg}")
            history_container.markdown("---")
    
    except Exception as e:
        st.error(e)