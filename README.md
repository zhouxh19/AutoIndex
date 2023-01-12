# Index_advisor
**Index_advisor** is a tool to recommend indexes for workload. A workload consists
of a set of SQL data manipulation statements, i.e., Select, Insert, Delete and Update.
First, some candidate indexes are generated based on query syntax and database
statistics. Then the optimal index set is determined by estimating the cost and
benefit of it for the workload.


Origin Repo Link (check the update-to-date functions): https://gitee.com/opengauss/openGauss-DBMind/tree/master/dbmind/components/index_advisor 

Document Link: https://docs.opengauss.org/en/docs/3.1.1/docs/Developerguide/index-advisor-index-recommendation.html


![alt text](./figures/arch.jpg?raw=true)

## Citing AutoIndex

```bibTeX
@inproceedings{autoindex2022,
	author    = {Xuanhe Zhou and Luyang Liu and Wenbo Li and Lianyuan Jin and Shifu Li and Tianqing Wang and Jianhua Feng},
	title     = {AutoIndex: An Incremental Index Management System for Dynamic Workloads},
	booktitle = {ICDE},
	year      = {2022}}
```
