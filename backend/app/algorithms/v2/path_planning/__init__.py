"""
Path Planning Engine V2: A* + Time Window + SIPP + Dynamic Replanning.

Upgrades from V1 (ACO-only):
- Bidirectional A* with heap priority queue (O((V+E)logV))
- Time Window reservation for collision-free multi-AGV routing
- SIPP (Safe Interval Path Planning) for state space reduction
- D* Lite variant for incremental replanning
"""
