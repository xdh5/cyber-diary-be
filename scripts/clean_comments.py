#!/usr/bin/env python3
"""清理重复的美食照片评论 - 简化版"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlmodel import Session, create_engine, select, func
from app.models.models import FoodPhotoComment
from app.core.config import settings

def clean_duplicate_comments():
    # 创建数据库连接
    engine = create_engine(settings.DATABASE_URL)
    
    with Session(engine) as session:
        # 查询重复的评论组
        duplicate_groups = session.exec(
            select(
                FoodPhotoComment.group_id,
                FoodPhotoComment.content,
                func.count(FoodPhotoComment.id).label('count')
            ).group_by(FoodPhotoComment.group_id, FoodPhotoComment.content).having(func.count(FoodPhotoComment.id) > 1)
        ).all()
        
        if not duplicate_groups:
            print("✅ 没有找到重复的评论")
            return
        
        print(f"⚠️ 找到 {len(duplicate_groups)} 组重复评论")
        
        delete_count = 0
        for group_id, content, _ in duplicate_groups:
            # 获取该组所有重复评论
            comments = session.exec(
                select(FoodPhotoComment)
                .where(FoodPhotoComment.group_id == group_id, FoodPhotoComment.content == content)
                .order_by(FoodPhotoComment.id)
            ).all()
            
            # 删除除第一条外的所有评论
            for comment in comments[1:]:
                session.delete(comment)
                delete_count += 1
        
        session.commit()
        print(f"✅ 已成功删除 {delete_count} 条重复评论")

if __name__ == "__main__":
    clean_duplicate_comments()
