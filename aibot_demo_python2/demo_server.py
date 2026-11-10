#!/usr/bin/env python
# coding=utf-8
# 文档：https://developer.work.weixin.qq.com/document/path/101039

import sys
import os
import logging
import json
import random
import string
import time
import base64
import hashlib
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from urlparse import urlparse, parse_qs
from WXBizJsonMsgCrypt import WXBizJsonMsgCrypt
from Crypto.Cipher import AES
import requests
import xml.etree.cElementTree as ET

# 常量定义
DEFAULT_PORT = 80
DEFAULT_HOST = '0.0.0.0'
CACHE_DIR = "/tmp/llm_demo_cache"
MAX_STEPS = 10

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
    
def _generate_random_string(length):
    letters = string.ascii_letters + string.digits
    return ''.join(random.choice(letters) for _ in range(length))

def _process_encrypted_image(image_url, aes_key_base64):
    """
    下载并解密加密图片
    
    参数:
        image_url: 加密图片的URL
        aes_key_base64: Base64编码的AES密钥(与回调加解密相同)
        
    返回:
        tuple: (status: bool, data: bytes/str) 
               status为True时data是解密后的图片数据，
               status为False时data是错误信息
    """
    try:
        # 1. 下载加密图片
        logger.info("开始下载加密图片: %s", image_url)
        response = requests.get(image_url, timeout=15)
        response.raise_for_status()
        encrypted_data = response.content
        logger.info("图片下载成功，大小: %d 字节", len(encrypted_data))
        
        # 2. 准备AES密钥和IV
        if not aes_key_base64:
            raise ValueError("AES密钥不能为空")
            
        # Base64解码密钥 (自动处理填充)
        aes_key = base64.b64decode(aes_key_base64 + "=" * (-len(aes_key_base64) % 4))
        if len(aes_key) != 32:
            raise ValueError("无效的AES密钥长度: 应为32字节")
            
        iv = aes_key[:16]  # 初始向量为密钥前16字节
        
        # 3. 解密图片数据
        cipher = AES.new(aes_key, AES.MODE_CBC, iv)
        decrypted_data = cipher.decrypt(encrypted_data)
        
        # 4. 去除PKCS#7填充
        pad_len = ord(decrypted_data[-1])
        if pad_len > 32:  # AES-256块大小为32字节
            raise ValueError("无效的填充长度")
            
        decrypted_data = decrypted_data[:-pad_len]
        logger.info("图片解密成功，解密后大小: %d 字节", len(decrypted_data))
        
        return True, decrypted_data
        
    except requests.exceptions.RequestException as e:
        error_msg = "图片下载失败"
        logger.error(error_msg)
        return False, error_msg
        
    except ValueError as e:
        error_msg = "参数错误"
        logger.error(error_msg)
        return False, error_msg
        
    except Exception as e:
        error_msg = "图片处理异常"
        logger.error(error_msg)
        return False, error_msg


# TODO 这里模拟一个大模型的行为
class LLMDemo():
    def __init__(self):
        self.cache_dir = CACHE_DIR
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

    def invoke(self, question):
        stream_id = _generate_random_string(10) # 生成一个随机字符串作为任务ID
        # 创建任务缓存文件
        cache_file = os.path.join(self.cache_dir, "%s.json" % stream_id)
        with open(cache_file, 'wb') as f:
            json.dump({
                'question': question,
                'created_time': time.time(),
                'current_step': 0,
                'max_steps': MAX_STEPS
            }, f)
        return stream_id

    def get_answer(self, stream_id):
        cache_file = os.path.join(self.cache_dir, "%s.json" % stream_id)
        if not os.path.exists(cache_file):
            return "任务不存在或已过期"
            
        with open(cache_file, 'rb') as f:
            task_data = json.load(f)
        
        # 更新缓存
        current_step = task_data['current_step'] + 1
        task_data['current_step'] = current_step
        with open(cache_file, 'wb') as f:
            json.dump(task_data, f)
            
        response = '收到问题：%s\n' % task_data['question']
        for i in range(current_step):
            response += '处理步骤 %d: 已完成\n' % (i)

        return response

    def is_task_finish(self, stream_id):
        cache_file = os.path.join(self.cache_dir, "%s.json" % stream_id)
        if not os.path.exists(cache_file):
            return True
            
        with open(cache_file, 'rb') as f:
            task_data = json.load(f)
            
        return task_data['current_step'] >= task_data['max_steps']


# http://localhost:80/ai-bot/callback/demo/{botid}
class RequestHandler(BaseHTTPRequestHandler):
    #实现VeifyUrl的GET请求
    def do_GET(self):
        url_detail = urlparse(self.path)
        if '/ai-bot/callback/demo' not in url_detail.path:
            logger.error('Invalid URL: %s', url_detail.path)
            self.send_error(404, "invalid url")
            return

        path_parts = url_detail.path.strip('/').split('/')
        logger.debug('Path parts: %s', path_parts)

        botid = ''
        if len(path_parts) > 3:
            botid = path_parts[3]

        logger.info('Bot ID: %s', botid)
        args = parse_qs(url_detail[4])
        logger.debug('Query args: %s', args)

        if len(args) < 3:
            logger.error('invalid args : %s', args)
            self.send_error(200, "invalid args")
            return
        logger.debug('path parts: %s', path_parts)

        # 企业创建的自能机器人的 VerifyUrl 请求, receiveid 是空串
        receiveid = ''

        #初始化加密类，注意 receiveid 在不同场景的含义不一样
        wxcpt = WXBizJsonMsgCrypt(os.getenv('Token', ''), os.getenv('EncodingAESKey', ''), receiveid)

        #解密出明文的echostr
        ret, echostr = wxcpt.VerifyURL(
            args['msg_signature'][0],
            args['timestamp'][0],
            args['nonce'][0],
            args['echostr'][0])

        logger.info("Verify result: ret=%d, echostr=%s", ret, echostr)
        if ret != 0:
            echostr = "verify fail"

        #通过http response返回给企业微信后台，完成验证
        self.send_response(200)
        self.send_header("Content-Length", str(len(echostr)))
        self.end_headers()
        self.wfile.write(echostr)
        return

    def do_POST(self):
        url_detail = urlparse(self.path)
        if '/ai-bot/callback/demo' not in url_detail.path:
            logger.error('Invalid URL: %s', url_detail.path)
            self.send_error(404, "invalid url")
            return

        path_parts = url_detail.path.strip('/').split('/')
        logger.debug('Path parts: %s', path_parts)

        botid = ''
        if len(path_parts) > 3:
            botid = path_parts[3]
        logger.info('Bot ID: %s', botid)

        args = parse_qs(url_detail[4]);
        logger.debug('Query args: %s', args)

        if len(args) < 3:
            logger.error('invalid args : %s', args)
            self.send_error(400, "invalid args")
            return


        length = self.headers.get('Content-Length')
        if length is None:
            self.send_error(400, "Empty Content")
            return

        post_data = self.rfile.read(int(length))

        # 智能机器人的 receiveid 是空串
        receiveid = ''
        wxcpt = WXBizJsonMsgCrypt(os.getenv('Token', ''), os.getenv('EncodingAESKey', ''), receiveid)

        ret, msg = wxcpt.DecryptMsg(
            post_data,
            args['msg_signature'][0],
            args['timestamp'][0],
            args['nonce'][0])

        if ret != 0:
            self.send_error(400, "Decrypt fail")
            return

        #解密得到明文的消息体
        content = ""

        data = json.loads(msg)
        logger.debug('Decrypted data: %s', data)
        if not data.has_key('msgtype'):
            echostr = 'success'
            self.send_response(200)
            self.send_header("Content-Length", str(len(echostr)))
            self.end_headers()
            self.wfile.write(echostr)
            return

        msgtype = data['msgtype']
        if(msgtype == 'text'):
            content = data['text']['content']

            # 询问大模型产生回复
            llm = LLMDemo()
            stream_id = llm.invoke(content)
            answer = llm.get_answer(stream_id)
            finish = llm.is_task_finish(stream_id)

            stream = self.MakeTextStream(stream_id, answer, finish)
            self.SendAIBotStreamResp(receiveid, stream)
        elif (msgtype == 'stream'):  # case stream
            # 询问大模型最新的回复
            stream_id = data['stream']['id']
            llm = LLMDemo()
            answer = llm.get_answer(stream_id)
            finish = llm.is_task_finish(stream_id)

            stream = self.MakeTextStream(stream_id, answer, finish)
            self.SendAIBotStreamResp(receiveid, stream)
            return
        elif (msgtype == 'image'):
            # 从环境变量获取AES密钥
            aes_key = os.getenv('EncodingAESKey', '')  
            
            # 调用图片处理函数
            success, result = _process_encrypted_image(data['image']['url'], aes_key)
            if not success:
                logger.error("图片处理失败: %s", result)
                return

            # 这里简单处理直接原图回复
            decrypted_data = result
            stream_id = _generate_random_string(10)
            finish = True

            stream = self.MakeImageStream(stream_id, decrypted_data, finish)
            self.SendAIBotStreamResp(receiveid, stream)
            return
        elif (msgtype == 'mixed'):
            # TODO 处理图文混排消息
            logger.warning("需要支持mixed消息类型")
        elif (msgtype == 'event'):  
            # TODO 一些事件的处理
            logger.warning("需要支持event消息类型: %s", data)
            return
        else:
            logger.warning("不支持的消息类型: %s", msgtype)
            return

    def MakeTextStream(self, stream_id, content, finish):
        content = content.encode('utf-8')

        plain = {
                    "msgtype": "stream",
                    "stream": {
                        "id": stream_id,
                        "finish": finish, 
                        "content" : content
                    }
                }
        plain = json.dumps(plain, ensure_ascii=False).encode('utf-8')

        return plain

    def MakeImageStream(self, stream_id, image_data, finish):
        image_md5 = hashlib.md5(image_data).hexdigest()
        image_base64 = base64.b64encode(image_data).decode('utf-8')

        plain = {
                    "msgtype": "stream",
                    "stream": {
                        "id": stream_id,
                        "finish": finish, 
                        "msg_item": [
                            {
                                "msgtype": "image",
                                "image": {
                                    "base64": image_base64,
                                    "md5": image_md5 
                                }
                            }
                        ]
                    }
                }
        plain = json.dumps(plain)

        return plain

    def SendAIBotStreamResp(self, receiveid, stream):
        url_detail = urlparse(self.path)
        args = parse_qs(url_detail[4])

        wxcpt = WXBizJsonMsgCrypt(os.getenv('Token', ''), os.getenv('EncodingAESKey', ''), receiveid)
        ret, resp = wxcpt.EncryptMsg(stream, args['nonce'][0], args['timestamp'][0])
        if ret != 0:
            logger.error("加密失败，错误码: %d", ret)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

        stream_id = json.loads(stream)['stream']['id']
        finish = json.loads(stream)['stream']['finish']
        logger.info("回调处理完成, 返回加密的流消息, stream_id=%s, finish=%s", stream_id, finish)

        return


if __name__ == '__main__':
    #启动http服务
    logger.info("server started on port 80")
    server = HTTPServer(('0.0.0.0', 80), RequestHandler)
    server.serve_forever()
