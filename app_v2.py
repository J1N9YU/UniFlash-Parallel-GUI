import subprocess
import os
import re
import time
import threading
import shutil
from datetime import datetime
from xml.etree import ElementTree as ET
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ======================
# 配置参数 - 请根据实际情况修改以下内容
# ======================

# dslite.exe的路径
DSLITE_PATH = r"C:\ti\uniflash_9.2.0\deskdb\content\TICloudAgent\win\ccs_base\DebugServer\bin\dslite.exe"

# 设备扫描工具路径
XDSDFU_PATH = r"C:\ti\uniflash_9.2.0\deskdb\content\TICloudAgent\win\ccs_base\common\uscif\xds110\xdsdfu.exe"

# 母版ccxml文件路径（用于复制和修改）
MASTER_CCXML_PATH = r"TMS320F28P550SJ9_LaunchPad.ccxml"

# 生成的ccxml文件存放目录（自动创建）
GENERATED_CCXML_DIR = r"generated_ccxml"

# 要烧录的二进制文件路径（所有通道共用）
OUT_FILE = r"led_ex1_blinky.out"

# 初始通道数量
NUM_CHANNELS = 1

# ======================
# 全局状态管理
# ======================

# 各通道使用的ccxml文件（动态生成）
CCXML_FILES = ["" for _ in range(8)]  # 索引0对应通道1，以此类推

# 烧录状态：未开始、烧录中、烧录成功、烧录失败
channel_status = {i: "未开始" for i in range(1, 9)}

# 烧录计数
success_count = {i: 0 for i in range(1, 9)}
fail_count = {i: 0 for i in range(1, 9)}

# 烧录进程
processes = {}

# 是否有烧录在进行中
is_running = False

# 设备序列号
device_serials = []

# 线程锁
status_lock = threading.Lock()

# ======================
# 初始化操作
# ======================

def init_generated_dir():
    """初始化生成ccxml文件的目录"""
    if not os.path.exists(GENERATED_CCXML_DIR):
        os.makedirs(GENERATED_CCXML_DIR)
        app.logger.info(f"创建ccxml生成目录: {GENERATED_CCXML_DIR}")
    else:
        # 清理目录中已有的ccxml文件
        for file in os.listdir(GENERATED_CCXML_DIR):
            if file.endswith(".ccxml"):
                try:
                    os.remove(os.path.join(GENERATED_CCXML_DIR, file))
                except Exception as e:
                    app.logger.warning(f"清理旧ccxml文件失败: {str(e)}")

# 初始化生成目录
init_generated_dir()

# ======================
# CCXML文件处理函数
# ======================

def create_ccxml_with_serial(channel, serial):
    """
    根据母版ccxml文件创建带有指定序列号的新文件
    
    :param channel: 通道号
    :param serial: 烧录器序列号
    :return: (成功与否, 新文件路径/错误信息)
    """
    # 检查母版文件是否存在
    if not os.path.exists(MASTER_CCXML_PATH):
        return False, f"母版ccxml文件不存在: {MASTER_CCXML_PATH}"
    
    # 生成新文件名
    new_filename = f"channel_{channel}_serial_{serial}.ccxml"
    new_filepath = os.path.join(GENERATED_CCXML_DIR, new_filename)
    
    try:
        # 复制母版文件到新路径
        shutil.copy2(MASTER_CCXML_PATH, new_filepath)
        
        # 解析XML并修改序列号
        tree = ET.parse(new_filepath)
        root = tree.getroot()
        
        # 查找需要修改的节点
        # 处理可能的命名空间
        namespaces = {}
        if '}' in root.tag:
            namespace = root.tag.split('}')[0][1:]
            namespaces['ns'] = namespace
        
        # 先找到"Debug Probe Selection"节点
        probe_xpath = ".//ns:property[@id='Debug Probe Selection']" if namespaces else ".//property[@id='Debug Probe Selection']"
        probe_node = root.find(probe_xpath, namespaces)
        
        if not probe_node:
            return False, "未找到Debug Probe Selection节点"
        
        # 在该节点下找到序列号输入框节点
        serial_xpath = ".//ns:property[@id='-- Enter the serial number']" if namespaces else ".//property[@id='-- Enter the serial number']"
        serial_node = probe_node.find(serial_xpath, namespaces)
        
        if not serial_node:
            return False, "未找到序列号输入节点"
        
        # 修改序列号
        serial_node.set("Value", serial)
        
        # 保存修改
        tree.write(new_filepath, encoding='utf-8', xml_declaration=True)
        
        app.logger.info(f"已生成通道 {channel} 的ccxml文件: {new_filepath} (序列号: {serial})")
        return True, new_filepath
        
    except Exception as e:
        # 发生错误时清理文件
        if os.path.exists(new_filepath):
            os.remove(new_filepath)
        return False, f"生成ccxml文件失败: {str(e)}"

# ======================
# 烧录相关函数
# ======================

def _check_success_flag(output_file):
    """检查输出文件末尾是否包含Success"""
    try:
        with open(output_file, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(max(f.tell() - 1024, 0), os.SEEK_SET)
            content = f.read()
            return "success" in content.lower()
    except Exception as e:
        app.logger.error(f"检查成功标志时发生错误: {str(e)}")
        return False

def _run_dslite(channel):
    """运行单个通道的烧录进程"""
    global is_running, channel_status, success_count, fail_count
    
    # 检查通道对应的ccxml文件是否存在
    ccxml_file = CCXML_FILES[channel - 1]  # CCXML_FILES索引0对应通道1
    app.logger.error((ccxml_file))
    if not ccxml_file or not os.path.exists(ccxml_file):
        with status_lock:
            channel_status[channel] = "配置错误: 未找到ccxml文件"
        app.logger.error(f"通道 {channel} 未找到ccxml文件: {ccxml_file}")
        return
    
    output_file = f"flash_channel_{channel}.txt"
    
    try:
        # 更新状态
        with status_lock:
            channel_status[channel] = "烧录中"
            is_running = True
        
        # 构建命令
        cmd = [
            DSLITE_PATH,
            "flash",
            "-c", ccxml_file,
            "-e", "-f", "-v",
            OUT_FILE
        ]
        
        app.logger.info(f"通道 {channel} 执行命令: {' '.join(cmd)}")
        
        # 执行命令
        start_time = datetime.now()
        with open(output_file, 'w', encoding='utf-8') as f:
            process = subprocess.run(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=300  # 5分钟超时
            )
        
        # 检查结果
        success = _check_success_flag(output_file)
        duration = (datetime.now() - start_time).total_seconds()
        
        # 更新状态和计数
        with status_lock:
            if success:
                channel_status[channel] = "烧录成功"
                success_count[channel] += 1
            else:
                channel_status[channel] = f"烧录失败 (返回码: {process.returncode})"
                fail_count[channel] += 1
            
            # 检查是否还有运行中的通道
            is_running = any(status == "烧录中" for status in channel_status.values())
            
    except Exception as e:
        app.logger.error(f"通道 {channel} 烧录错误: {str(e)}")
        with status_lock:
            channel_status[channel] = f"烧录错误: {str(e)}"
            fail_count[channel] += 1
            is_running = any(status == "烧录中" for status in channel_status.values())

def start_single_channel(channel):
    """启动单个通道的烧录"""
    global processes
    
    with status_lock:
        if channel_status[channel] == "烧录中":
            return False, "该通道正在烧录中"
    
    # 创建并启动线程
    thread = threading.Thread(target=_run_dslite, args=(channel,), daemon=True)
    thread.start()
    processes[channel] = thread
    return True, f"通道 {channel} 烧录已启动"

def start_all_channels(num_channels):
    """启动所有通道的烧录"""
    global is_running
    
    with status_lock:
        if is_running:
            return False, "已有烧录任务在进行中"
    
    # 启动每个通道
    for channel in range(1, num_channels + 1):
        with status_lock:
            if channel_status[channel] != "烧录中":
                thread = threading.Thread(target=_run_dslite, args=(channel,), daemon=True)
                thread.start()
                processes[channel] = thread
                time.sleep(0.2)  # 稍微延迟，避免同时启动冲突
    
    return True, f"所有 {num_channels} 个通道烧录已启动"

def stop_all_channels():
    """停止所有通道的烧录"""
    global is_running, channel_status
    
    # 标记状态为终止
    with status_lock:
        for channel in processes:
            if channel_status[channel] == "烧录中":
                channel_status[channel] = "烧录终止"
                fail_count[channel] += 1
        
        is_running = False
    
    return True, "所有烧录任务已终止"

# ======================
# 设备扫描相关函数
# ======================

def scan_devices():
    """扫描连接的烧录器设备并生成对应的ccxml文件"""
    global device_serials, CCXML_FILES, NUM_CHANNELS
    
    try:
        # 检查xdsdfu是否存在
        if not os.path.exists(XDSDFU_PATH):
            return False, [], f"未找到设备扫描工具: {XDSDFU_PATH}"
        
        # 执行扫描命令
        result = subprocess.run(
            [XDSDFU_PATH, "-e"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        output = result.stdout + result.stderr
        
        # 解析输出，提取序列号
        serials = []
        pattern = r"Serial Num:\s+([A-Za-z0-9]+)"  # 匹配序列号格式
        matches = re.findall(pattern, output)
        
        if not matches:
            device_serials = []
            return True, [], "未找到任何烧录器设备"
        
        # 限制最大数量为8
        max_devices = min(len(matches), 8)
        serials = matches[:max_devices]
        device_serials = serials
        
        # 为每个设备生成对应的ccxml文件
        success_channels = 0
        for i in range(max_devices):
            channel = i + 1  # 通道号从1开始
            serial = serials[i]
            
            # 生成带有序列号的ccxml文件
            success, msg = create_ccxml_with_serial(channel, serial)
            if success:
                CCXML_FILES[i] = msg  # 更新通道对应的ccxml文件路径
                success_channels += 1
                app.logger.info(f"通道 {channel} 已关联序列号: {serial}")
            else:
                app.logger.error(f"通道 {channel} 关联序列号失败: {msg}")
        
        # 更新通道数量为成功关联的设备数
        if success_channels > 0:
            NUM_CHANNELS = success_channels
        
        return True, serials, f"成功找到 {len(serials)} 个设备，其中 {success_channels} 个已配置ccxml文件"
        
    except Exception as e:
        app.logger.error(f"设备扫描错误: {str(e)}")
        return False, [], f"扫描设备时发生错误: {str(e)}"

# ======================
# Flask路由
# ======================

@app.route('/')
def index():
    """前端页面"""
    return render_template('index.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    """获取配置信息"""
    return jsonify({
        "num_channels": NUM_CHANNELS,
        "dslite_path": DSLITE_PATH,
        "out_file": OUT_FILE,
        "ccxml_files": [CCXML_FILES[i] for i in range(NUM_CHANNELS)],
        "master_ccxml": MASTER_CCXML_PATH
    })

@app.route('/api/config', methods=['POST'])
def update_config():
    """更新配置信息"""
    global NUM_CHANNELS
    data = request.json
    
    if "num_channels" in data:
        new_num = int(data["num_channels"])
        if 1 <= new_num <= 8:
            NUM_CHANNELS = new_num
            return jsonify({
                "status": "success",
                "message": f"通道数已更新为 {NUM_CHANNELS}"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "通道数必须在1-8之间"
            })
    
    return jsonify({
        "status": "error",
        "message": "无效的配置参数"
    })

@app.route('/api/status', methods=['GET'])
def get_status():
    """获取当前状态"""
    with status_lock:
        return jsonify({
            "is_running": is_running,
            "num_channels": NUM_CHANNELS,
            "channels": {k: v for k, v in channel_status.items() if k <= NUM_CHANNELS},
            "counters": {
                "success": {k: v for k, v in success_count.items() if k <= NUM_CHANNELS},
                "fail": {k: v for k, v in fail_count.items() if k <= NUM_CHANNELS}
            },
            "serials": device_serials,
            "ccxml_files": {i+1: CCXML_FILES[i] for i in range(NUM_CHANNELS)}  # 通道号对应ccxml文件
        })

@app.route('/api/start', methods=['POST'])
def start_flash():
    """开始所有通道烧录"""
    success, message = start_all_channels(NUM_CHANNELS)
    return jsonify({
        "status": "success" if success else "error",
        "message": message
    })

@app.route('/api/start/<int:channel>', methods=['POST'])
def start_single_flash(channel):
    """开始单个通道烧录"""
    if 1 <= channel <= NUM_CHANNELS:
        success, message = start_single_channel(channel)
        return jsonify({
            "status": "success" if success else "error",
            "message": message
        })
    else:
        return jsonify({
            "status": "error",
            "message": f"无效的通道号: {channel}"
        })

@app.route('/api/stop', methods=['POST'])
def stop_flash():
    """停止所有通道烧录"""
    success, message = stop_all_channels()
    return jsonify({
        "status": "success" if success else "error",
        "message": message
    })

@app.route('/api/scan', methods=['POST'])
def scan():
    """扫描设备并生成ccxml文件"""
    success, serials, message = scan_devices()
    return jsonify({
        "status": "success" if success else "error",
        "message": message,
        "serials": serials,
        "count": len(serials),
        "configured_count": sum(1 for i in range(len(serials)) if CCXML_FILES[i] and os.path.exists(CCXML_FILES[i]))
    })

# ======================
# 程序入口
# ======================

if __name__ == '__main__':
    # 验证关键文件是否存在
    if not os.path.exists(DSLITE_PATH):
        app.logger.warning(f"dslite.exe 未找到: {DSLITE_PATH}")
    
    if not os.path.exists(OUT_FILE):
        app.logger.warning(f"烧录文件未找到: {OUT_FILE}")
    
    if not os.path.exists(MASTER_CCXML_PATH):
        app.logger.error(f"母版ccxml文件不存在: {MASTER_CCXML_PATH} - 请检查配置")
    
    if not os.path.exists(XDSDFU_PATH):
        app.logger.warning(f"xdsdfu.exe 未找到: {XDSDFU_PATH}")
    
    # 启动Flask应用
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
