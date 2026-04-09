def get_chip_series(chip_model: str) -> str:
    """
    根据芯片型号判断芯片系列
    :param chip_model: 芯片型号字符串 (如 F28P55, MSPM0G5187)
    :return: C2000 / MSP / 空字符串
    """
    if not chip_model:
        return ""
    
    chip_model_upper = chip_model.upper()
    
    if chip_model_upper.startswith("F28"):
        return "C2000"
    elif chip_model_upper.startswith("MSP"):
        return "MSP"
    else:
        return ""