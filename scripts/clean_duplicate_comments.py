#!/usr/bin/env python3
"""清理重复的美食照片评论"""

from sqlmodel import Session, create_engine, select, func
from app.models.models import FoodPhotoComment
from app.core.config import settings

def main():
    # 创建数据库连接
    engine = create_engine(settings.DATABASE_URL)
    
    with Session(engine) as session:
        # 查询所有评论，按 group_id 和 content 分组
        statement = select(
            FoodPhotoComment.group_id,
            FoodPhotoComment.content,
            func.count(FoodPhotoComment.id).label('count')
        ).group_by(FoodPhotoComment.group_id, FoodPhotoComment.content).having(func.count(FoodPhotoComment.id) > 1)
        
        duplicates = session.exec(statement).all()
        
        if not duplicates:
            print("没有找到重复的评论")
            return
        
        print(f"找到 {len(duplicates)} 组重复评论:")
        for group_id, content, count in duplicates:
            print(f"  group_id: {group_id[:8]}..., content: {content[:50]}..., 重复次数: {count}")
        
        # 删除重复评论，保留每组的第一条
        delete_count = 0
        for group_id, content, _ in duplicates:
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
        print(f"\n已删除 {delete_count} 条重复评论")

if __name__ == "__main__":
    main()
