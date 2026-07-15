from __future__ import annotations

import argparse
import math
import time
from pathlib import Path
from typing import Optional, Sequence

from parahand import ParaHand


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ParaHand 控制与双模式触觉读取示例")
    parser.add_argument(
        "--tactile-mode",
        choices=("fsr402", "hwk"),
        default=None,
        help="覆盖 config_hand.yaml 中的 tactile.mode",
    )
    parser.add_argument(
        "--tactile-port",
        default=None,
        help="覆盖当前触觉模式配置的串口，例如 COM8",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    config_path = Path(__file__).with_name("config_hand.yaml")
    hand = ParaHand(str(config_path))

    print(f"使用配置: {hand.config_path}")
    print("关节顺序:")
    for index, joint_name in enumerate(hand.get_hand_joint_order()):
        print(f"  {index:02d}: {joint_name}")

    if not hand.connect():
        print("连接失败")
        return 1

    try:
        if args.tactile_mode is not None:
            tactile_ok = hand.start_tactile(
                mode=args.tactile_mode,
                port=args.tactile_port,
            )
            if not tactile_ok:
                print(f"触觉传感器启动失败: {hand.last_tactile_error}")

        print("连接成功，开始轮询反馈")
        hand.start_polling()
        time.sleep(0.2)

        print("全局使能已启用关节")
        hand.enable()
        time.sleep(0.2)

        print("\n示例 1：按关节名发送目标角度（单位：度）")
        command_ids = hand.set_joint_positions(
            {
                "thumb.cmc_1": 5.0,
                "index.mcp_2": 20.0,
            }
        )
        print("命令ID:", command_ids)
        time.sleep(0.5)

        print("\n示例 2：发送整手位置（普通关节单位：rad，pip_dip 单位：m）")
        positions = [0.0] * len(hand.get_hand_joint_order())
        positions[0] = math.radians(10.0)   # thumb.cmc_1
        positions[5] = math.radians(25.0)   # index.mcp_2
        positions[6] = 0.006                # index.pip_dip，单位 m
        command_ids = hand.set_hand_positions(positions)
        print("命令ID:", command_ids)
        time.sleep(0.5)

        print("\n示例 3：读取反馈")
        joint_feedback = hand.get_joint_feedback()
        for joint_name, feedback in joint_feedback.items():
            position_deg = feedback.get("position_deg")
            online = feedback.get("online")
            print(f"  {joint_name}: position_deg={position_deg}, online={online}")

        print("\n示例 4：按关节读取当前位置（单位：度）")
        print(hand.get_joint_positions())

        print("\n示例 5：读取触觉数据")
        snapshot = hand.get_tactile_snapshot()
        if snapshot is None:
            print("尚无触觉数据；请检查 tactile.enabled、mode 和串口配置")
        else:
            print(f"模式: {snapshot.mode}, 代表值单位: {snapshot.unit}")
            print("五指代表值:", hand.get_tactile_values())
            if snapshot.mode == "hwk":
                matrix_shapes = {
                    region: [
                        (len(matrix), len(matrix[0]) if matrix else 0)
                        for matrix in matrices
                    ]
                    for region, matrices in snapshot.matrices.items()
                }
                print("华威科各区域矩阵尺寸:", matrix_shapes)
                print("华威科各区域力汇总 (g):", hand.get_tactile_force_summaries())
            else:
                print("FSR402 原始帧:", hand.get_tactile_frame())

        print("\n示例 6：单个关节调零接口（实际走 jog/零位流程请按硬件协议使用）")
        print("这里仅演示调用，不默认执行。")
        print("例如：hand.jog_joint('thumb.cmc_1', 1) / hand.jog_joint('thumb.cmc_1', 0)")

        print("\n示例结束")
        return 0
    finally:
        print("关闭使能并断开连接")
        try:
            hand.disable()
        except Exception:
            pass
        hand.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
