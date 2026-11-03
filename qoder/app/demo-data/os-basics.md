# OceanBase 与存储基础笔记

## 数据库基本组成

一个数据库系统通常由存储引擎、事务管理、查询优化器与执行器组成。OceanBase 是蚂蚁集团自研的分布式关系数据库，兼容 MySQL 协议，支持 HTAP 混合负载。数据以分区（partition）方式分布在多个节点上，通过 Paxos 协议保证多副本一致性。

## 存储引擎与 LSM-Tree

OceanBase 的存储引擎基于 LSM-Tree 结构，分为内存中的 MemTable 与磁盘上的 SSTable 两层。写入先进 MemTable，达到阈值后冻结并转储为 SSTable，这一过程称为 minor compaction。多数 SSTable 合并为基线数据的过程称为 major compaction，通常在每日低峰期执行。

## 事务与一致性

OceanBase 支持完整 ACID 事务，默认隔离级别为读已提交（Read Committed）。分布式事务采用两阶段提交（2PC），协调者记录 prepare 日志后等待参与者响应。全局时间戳由 GTS（Global Timestamp Service）统一分配，保证快照读的一致性。

## 向量检索与 seekdb

seekdb 是面向 AI 场景的数据库能力，支持向量索引与混合检索。本项目使用嵌入式 pyseekdb，通过 HNSW 算法建立向量索引，距离度量采用 cosine 余弦相似度。查询时把文本 embedding 成 256 维向量，再计算与资料块向量的相似度，取 Top-K 返回。

## 两组件分工

在本项目中，seekdb 与 PowerContext 各司其职、互不重叠。seekdb 负责知识检索：导入的资料切块后建立向量索引，复习时执行向量相似、关键词匹配、元数据结构化过滤三路混合检索，并给出出处引用。PowerContext 负责学习者记忆：把错题、薄弱点、复习偏好与进度作为 Memory 长期保存，下次打开时通过 context/prepare 端点取回历史上下文。资料本身不写入 PowerContext，避免与 seekdb 职责重叠。

## 混合检索三路信号

混合检索融合三路信号。第一路是向量检索，用 cosine 相似度衡量语义接近程度。第二路是关键词匹配，统计查询关键词在资料块中的命中率。第三路是结构化过滤，按 source 文件名或 title 标题用 metadata 的 where 条件筛选。最终得分公式为 score = (1 - w) * 向量相似 + w * 关键词命中，默认权重 w 取 0.4。

## 幂等与去重

导入资料需要保证幂等：重复导入同一文件名时，先按 source 元数据删除旧块，再写入新块。块的唯一 id 由 source、index 与文本内容的 md5 前 12 位生成，形如 doc_ 加 12 位十六进制。这样同一份资料多次导入不会产生重复索引。

## 嵌入式部署注意

pyseekdb 的 wheel 仅提供 Linux x86_64 版本，因此后端必须运行在 Linux 或 WSL2 中。seekdb 数据库目录必须位于本地文件系统，不能放在 /mnt/c 等 Windows 挂载路径，否则会报 IO 或锁定错误。数据库以库形式内嵌在应用进程中，无需单独部署服务。

## 记忆与跨会话续接

PowerContext 通过 HTTP API 提供记忆能力。写入薄弱点用 memory/remember 端点，kind 取 observation；写入复习偏好用 kind 取 decision。检索记忆用 memory/search 端点。取回有界历史上下文用 context/prepare 端点，max_bytes 默认 4000，响应 status 为 ready 表示有历史、empty 表示首次使用。

## 复习闭环

完整复习闭环为：导入资料、带出处问答、生成复习卡、用户自评、写入薄弱点、下次续接。自评结果分为会、模糊、不会三档，标记为不会或模糊的知识点会被归纳为薄弱点，在下次出题时提高优先级，优先从薄弱点对应的资料来源出题。
