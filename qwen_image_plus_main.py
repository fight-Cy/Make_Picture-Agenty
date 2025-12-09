import streamlit as st
import os
from dotenv import load_dotenv
from dashscope import Generation, ImageSynthesis  # 导入官方ImageSynthesis类
from dashscope.api_entities.dashscope_response import Role
from langchain.memory import ConversationBufferMemory
from http import HTTPStatus  # 官方示例依赖
from urllib.parse import urlparse, unquote
from pathlib import PurePosixPath
import requests

# 1. 加载环境变量+配置阿里云地域
load_dotenv()
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not DASHSCOPE_API_KEY:
    st.error("❌ 请在.env文件中配置DASHSCOPE_API_KEY")
    st.stop()

# 配置地域（北京地域，若用新加坡需改为：https://dashscope-intl.aliyuncs.com/api/v1）
ImageSynthesis.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
Generation.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'  # 通义千问也同步配置地域


# 2. 多轮对话记忆
class ConversationMemory:
    def __init__(self):
        self.memory = ConversationBufferMemory(
            memory_key="chat_history",
            return_messages=True,
            human_prefix="用户需求",
            ai_prefix="优化后Prompt"
        )
    
    def get_history(self):
        return self.memory.load_memory_variables({})["chat_history"]
    
    def add_history(self, user_input, optimized_prompt):
        self.memory.save_context({"input": user_input}, {"output": optimized_prompt})

conv_memory = ConversationMemory()


# 3. 通义千问Prompt优化
def qwen_optimize_prompt(user_input):
    system_prompt = """
你是资深AI绘图提示词工程师，专注于生成适配qwen-image-plus模型的高质量中文提示词。
要求：1. 包含主体+场景+细节（如材质、光影）；2. 多轮修改时保留原主体；3. 仅返回提示词，无额外解释。
    """
    messages = [{"role": Role.SYSTEM, "content": system_prompt}]
    # 加入历史对话
    for msg in conv_memory.get_history():
        role = Role.USER if msg.type == "human" else Role.ASSISTANT
        messages.append({"role": role, "content": msg.content})
    messages.append({"role": Role.USER, "content": user_input})
    
    # 调用通义千问（同步地域配置）
    try:
        response = Generation.call(
            model="qwen-turbo",
            api_key=DASHSCOPE_API_KEY,
            messages=messages,
            temperature=0.7,
            result_format="message"
        )
        optimized_prompt = response.output.choices[0].message.content.strip()
        conv_memory.add_history(user_input, optimized_prompt)
        return optimized_prompt
    except Exception as e:
        raise Exception(f"❌ 通义千问优化失败：{str(e)}")


# 4. 通义千问-图像增强版调用
def call_qwen_image_plus(optimized_prompt):
    """
    严格使用官方ImageSynthesis.call方法，避免手动HTTP请求的格式错误
    参考官方示例：https://help.aliyun.com/document_detail/2710792.html
    """
    try:
        # 直接调用官方封装的ImageSynthesis.call
        rsp = ImageSynthesis.call(
            api_key=DASHSCOPE_API_KEY,
            model="qwen-image-plus",  # 官方模型ID
            prompt=optimized_prompt,  # 通义千问优化后的提示词
            n=1,  # 生成1张图
            size='1328*1328',  # 官方允许的尺寸
            prompt_extend=True,  # 开启Prompt扩展
            watermark=True  # 按示例开启水印（可改为False）
        )
        
        # 解析响应
        if rsp.status_code == HTTPStatus.OK:
            # 从响应中提取图片URL
            result = rsp.output.results[0]  # 取第一张图
            image_url = result.url
            # （可选）按示例逻辑保存图片到本地
            file_name = PurePosixPath(unquote(urlparse(image_url).path)).parts[-1]
            with open(f'./{file_name}', 'wb+') as f:
                f.write(requests.get(image_url).content)
            return image_url
        else:
            # 官方示例的错误打印逻辑
            raise Exception(f"模型调用失败：状态码{rsp.status_code}，错误{rsp.code}：{rsp.message}")
    
    except Exception as e:
        raise Exception(f"❌ 图片生成失败：{str(e)}")


# 5. Streamlit界面
st.title("AI 画图助手（通义千问+qwen-image-plus 官方版）")
user_input = st.text_input("请输入绘图需求（例：中式厅堂+对联+岳阳楼画）：")

if st.button("生成图片") and user_input.strip():
    # 优化Prompt
    with st.spinner("优化Prompt..."):
        try:
            optimized_prompt = qwen_optimize_prompt(user_input)
            st.subheader("优化后的提示词")
            st.code(optimized_prompt)
        except Exception as e:
            st.error(e)
            st.stop()
    
    # 生成图片
    with st.spinner("生成图片..."):
        try:
            image_url = call_qwen_image_plus(optimized_prompt)
            st.subheader("生成结果")
            st.image(image_url, use_column_width=True)
            # 下载功能
            st.download_button(
                label="下载图片",
                data=requests.get(image_url).content,
                file_name="qwen_image_plus.png",
                mime="image/png"
            )
        except Exception as e:
            st.error(e)
            st.stop()