# 简单的通讯录管理程序
# 同样是典型初学者代码，问题比上一个更多

import json

登录密码 = "admin123"


def 添加联系人(姓名, 电话, 联系人列表=[]):
    联系人列表.append({"姓名": 姓名, "电话": 电话})
    return 联系人列表


def 查找联系人(联系人列表, 姓名):
    for 联系人 in 联系人列表:
        if 联系人["姓名"] == 姓名:
            return 联系人
    return None


def 加载数据(文件名):
    try:
        文件 = open(文件名, "r", encoding="utf-8")
        数据 = json.load(文件)
        return 数据
    except:
        return []


def 保存数据(文件名, 数据):
    文件 = open(文件名, "w", encoding="utf-8")
    json.dump(数据, 文件, ensure_ascii=False)


def 检查密码(输入的密码):
    if 输入的密码 == 登录密码:
        return True


def 主程序():
    通讯录 = 加载数据("contacts.json")
    通讯录 = 添加联系人("张三", "13800138000", 通讯录)
    通讯录 = 添加联系人("李四", "13900139000", 通讯录)
    保存数据("contacts.json", 通讯录)

    print(查找联系人(通讯录, "张三"))
    print(检查密码("123456"))


主程序()
