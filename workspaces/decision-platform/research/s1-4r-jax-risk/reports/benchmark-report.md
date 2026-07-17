# S1.4R bounded benchmark report / 제한형 벤치마크 보고서

## Question and non-goals / 질문과 비목표

- KR: 동일한 canonical fixture와 512 MiB data-working-set 한도에서 검증형 NumPy reference와 JAX CPU/x64 compiled numeric core의 cold 단계와 steady-state 특성을 분리해 관찰한다.
- EN: Observe cold phases and steady-state behavior of the validated NumPy reference and JAX CPU/x64 compiled numeric core with identical canonical fixtures under a 512 MiB data-working-set cap.
- KR 비목표: production 교체 결정, 성능 합격선, GPU/grad, annualization, 서로 다른 timed boundary 사이의 speedup 주장.
- EN non-goals: production replacement, performance thresholds, GPU/grad, annualization, or speedup claims across different timed boundaries.

## Run and matrix / 실행과 행렬

- Matrix: `full`
- Cases: 62 (results: 124)
- Run ID: `run-20260717T155054Z-030da729`
- Git commit: `60ed803fb0a327ee1dbc546920464ce5693c90a9`
- Created UTC: `2026-07-17T16:13:47.142746Z`
- Plan SHA-256: `9e9e6b06c360ad0dd156907fc75dfb4faf2b57e1b204d58cebb80bb72d241700`
- Raw samples SHA-256: `5592db44c80402d83750dda99840106762427ae3797d77174a03c54fa1531c11`
- Two axes: one-dimensional sizes `[32, 252, 1000, 10000, 100000]`; path batches `[100, 1000, 10000] x horizon 252` without Cartesian materialization.
- Protocol: 20 fresh cold processes, 5 untimed warmups, 50 timed warm samples, NumPy `linear` quantiles.
- Allocation cap: 536870912 bytes.

## Correctness and timing boundaries / 정확성과 측정 경계

- Every generated fixture was checked over all valid evaluations against the same chunked JIT/vmap numeric path before timing (`rtol=1e-10`, `atol=1e-12`).
- NumPy timing boundary: `validated_public_reference`; JAX timing boundary: `compiled_device_numeric_core`. Validation, fixture generation, JSON/file decode, and JAX transfers are excluded from JAX warm execution.

Latency values are `p50 / p95` nanoseconds.

| Case | Impl | Boundary | First call | Trace/lower | Compile | H→D | First execute | D→H | Cold total | Warm | Max abs error | Max rel error | Max tolerance ratio |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `historical_expected_shortfall--n-32` | `numpy` | `validated_public_reference` | 6748203.500 / 10962359.600 | N/A | N/A | N/A | N/A | N/A | 6748203.500 / 10962359.600 | 9803.000 / 13180.550 | 0 | 0 | 0 |
| `historical_expected_shortfall--n-32` | `jax_jit` | `compiled_device_numeric_core` | N/A | 56663719.000 / 102218572.450 | 74361331.000 / 117443857.400 | 772015.500 / 2639082.000 | 361452.500 / 1129339.950 | 92243.500 / 325066.800 | 139850304.500 / 260226769.150 | 20542.000 / 25820.900 | 0 | 0 | 0 |
| `historical_expected_shortfall--n-252` | `numpy` | `validated_public_reference` | 7843797.000 / 10695792.200 | N/A | N/A | N/A | N/A | N/A | 7843797.000 / 10695792.200 | 24874.000 / 29153.900 | 6.93889e-18 | 1.50993e-16 | 1.24008e-06 |
| `historical_expected_shortfall--n-252` | `jax_jit` | `compiled_device_numeric_core` | N/A | 53661954.500 / 109266741.500 | 125808124.500 / 181777513.000 | 782471.000 / 1888975.150 | 373101.500 / 732355.350 | 85463.500 / 224645.000 | 185301967.500 / 327556530.700 | 39546.000 / 93922.550 | 6.93889e-18 | 1.50993e-16 | 1.24008e-06 |
| `historical_expected_shortfall--n-1000` | `numpy` | `validated_public_reference` | 6393581.000 / 10851006.500 | N/A | N/A | N/A | N/A | N/A | 6393581.000 / 10851006.500 | 76736.000 / 99361.100 | 6.93889e-18 | 1.51949e-16 | 1.24653e-06 |
| `historical_expected_shortfall--n-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 61670873.000 / 126554132.900 | 132485209.000 / 306068700.300 | 779210.000 / 1524099.650 | 520172.000 / 1239536.550 | 103071.500 / 353138.800 | 204622822.000 / 407543448.800 | 143514.500 / 230628.950 | 6.93889e-18 | 1.51949e-16 | 1.24653e-06 |
| `historical_expected_shortfall--n-10000` | `numpy` | `validated_public_reference` | 8761747.500 / 11088276.400 | N/A | N/A | N/A | N/A | N/A | 8761747.500 / 11088276.400 | 796785.500 / 1351589.500 | 0 | 0 | 0 |
| `historical_expected_shortfall--n-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 51121916.500 / 61335335.550 | 96165703.000 / 151501015.450 | 813701.500 / 1551016.450 | 3089684.000 / 12594306.650 | 145018.000 / 276372.550 | 158282458.000 / 225470214.250 | 1721623.000 / 2553009.850 | 0 | 0 | 0 |
| `historical_expected_shortfall--n-100000` | `numpy` | `validated_public_reference` | 22295791.500 / 37234988.750 | N/A | N/A | N/A | N/A | N/A | 22295791.500 / 37234988.750 | 10558640.500 / 13522527.500 | 1.38778e-17 | 2.97443e-16 | 2.44944e-06 |
| `historical_expected_shortfall--n-100000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 61224931.500 / 92858337.100 | 96784712.000 / 140060127.250 | 781088.000 / 1498780.850 | 22736658.500 / 30609545.050 | 230189.500 / 307039.100 | 179443175.500 / 266336301.800 | 17996756.500 / 22402170.100 | 1.38778e-17 | 2.97443e-16 | 2.44944e-06 |
| `realized_variance--n-32` | `numpy` | `validated_public_reference` | 6450959.000 / 7806646.200 | N/A | N/A | N/A | N/A | N/A | 6450959.000 / 7806646.200 | 3688.000 / 4772.400 | 1.73472e-18 | 1.2221e-16 | 7.16988e-07 |
| `realized_variance--n-32` | `jax_jit` | `compiled_device_numeric_core` | N/A | 22944199.500 / 30792286.200 | 24594848.500 / 31354180.700 | 805328.500 / 2847021.800 | 281505.500 / 440307.950 | 90879.000 / 121890.650 | 53684739.500 / 70721160.200 | 9477.500 / 14850.350 | 1.73472e-18 | 1.2221e-16 | 7.16988e-07 |
| `realized_variance--n-252` | `numpy` | `validated_public_reference` | 7392037.500 / 9705400.800 | N/A | N/A | N/A | N/A | N/A | 7392037.500 / 9705400.800 | 3842.000 / 5587.200 | 0 | 0 | 0 |
| `realized_variance--n-252` | `jax_jit` | `compiled_device_numeric_core` | N/A | 20878730.500 / 38359655.200 | 63847897.500 / 119881182.600 | 816874.500 / 1045364.050 | 313005.000 / 381270.250 | 96627.500 / 125513.200 | 89515402.500 / 166571140.650 | 11389.500 / 32190.200 | 0 | 0 | 0 |
| `realized_variance--n-1000` | `numpy` | `validated_public_reference` | 6315269.000 / 11054987.950 | N/A | N/A | N/A | N/A | N/A | 6315269.000 / 11054987.950 | 4684.500 / 5735.300 | 5.55112e-17 | 1.40133e-16 | 1.36683e-06 |
| `realized_variance--n-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 20018151.500 / 32218487.250 | 59957135.000 / 121501948.200 | 853768.000 / 1111687.400 | 341265.500 / 421943.850 | 96218.500 / 182045.550 | 86427443.000 / 163113828.100 | 17254.000 / 25553.300 | 5.55112e-17 | 1.40133e-16 | 1.36683e-06 |
| `realized_variance--n-10000` | `numpy` | `validated_public_reference` | 7306454.500 / 11286233.450 | N/A | N/A | N/A | N/A | N/A | 7306454.500 / 11286233.450 | 10992.000 / 13415.300 | 8.88178e-16 | 2.19024e-16 | 2.18485e-06 |
| `realized_variance--n-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 18726123.000 / 24917699.850 | 8421686.500 / 11063251.750 | 793202.500 / 979988.800 | 803977.000 / 3525296.450 | 97492.000 / 206724.950 | 33468168.500 / 43770931.150 | 19146.500 / 112928.800 | 8.88178e-16 | 2.19024e-16 | 2.18485e-06 |
| `realized_variance--n-100000` | `numpy` | `validated_public_reference` | 7495064.500 / 11175857.650 | N/A | N/A | N/A | N/A | N/A | 7495064.500 / 11175857.650 | 584598.500 / 1112243.200 | 0 | 0 | 0 |
| `realized_variance--n-100000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 20079636.000 / 27474400.800 | 8302183.000 / 10555011.150 | 766162.000 / 997393.000 | 1377730.000 / 3431078.350 | 118698.500 / 210872.900 | 36125733.500 / 51989388.200 | 33146.500 / 54317.550 | 0 | 0 | 0 |
| `realized_volatility_intraday--n-32` | `numpy` | `validated_public_reference` | 7046293.000 / 8763078.250 | N/A | N/A | N/A | N/A | N/A | 7046293.000 / 8763078.250 | 5338.000 / 9570.600 | 1.38778e-17 | 1.19929e-16 | 1.1039e-06 |
| `realized_volatility_intraday--n-32` | `jax_jit` | `compiled_device_numeric_core` | N/A | 20440247.000 / 23819216.350 | 23707318.500 / 29392585.850 | 748600.000 / 899890.050 | 254687.500 / 378016.650 | 86568.500 / 108377.150 | 50082343.500 / 58139676.450 | 9776.000 / 20186.450 | 1.38778e-17 | 1.19929e-16 | 1.1039e-06 |
| `realized_volatility_intraday--n-252` | `numpy` | `validated_public_reference` | 6340332.500 / 8418692.950 | N/A | N/A | N/A | N/A | N/A | 6340332.500 / 8418692.950 | 5046.500 / 5430.650 | 0 | 0 | 0 |
| `realized_volatility_intraday--n-252` | `jax_jit` | `compiled_device_numeric_core` | N/A | 20425257.500 / 49096735.250 | 67287111.500 / 138559007.950 | 857282.000 / 2128053.700 | 302746.000 / 644570.950 | 95969.000 / 142857.300 | 98182707.500 / 199953975.850 | 8103.500 / 16909.100 | 0 | 0 | 0 |
| `realized_volatility_intraday--n-1000` | `numpy` | `validated_public_reference` | 6951847.000 / 8727041.400 | N/A | N/A | N/A | N/A | N/A | 6951847.000 / 8727041.400 | 4768.500 / 6757.150 | 1.11022e-16 | 1.7681e-16 | 1.74038e-06 |
| `realized_volatility_intraday--n-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 18426342.000 / 31836777.950 | 59597176.000 / 84432280.700 | 768534.000 / 1154633.500 | 309879.500 / 502345.450 | 89016.500 / 301670.050 | 84775270.000 / 123497734.200 | 15810.000 / 19574.150 | 1.11022e-16 | 1.7681e-16 | 1.74038e-06 |
| `realized_volatility_intraday--n-10000` | `numpy` | `validated_public_reference` | 7067951.500 / 8581478.050 | N/A | N/A | N/A | N/A | N/A | 7067951.500 / 8581478.050 | 11497.000 / 13596.800 | 0 | 0 | 0 |
| `realized_volatility_intraday--n-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 18866535.000 / 33891101.200 | 23455974.500 / 33462034.850 | 828992.000 / 1010026.200 | 842746.000 / 1093446.700 | 112660.000 / 169536.150 | 47767349.000 / 75668398.350 | 20884.500 / 31719.450 | 0 | 0 | 0 |
| `realized_volatility_intraday--n-100000` | `numpy` | `validated_public_reference` | 7816836.000 / 10575201.450 | N/A | N/A | N/A | N/A | N/A | 7816836.000 / 10575201.450 | 696711.000 / 912656.700 | 0 | 0 | 0 |
| `realized_volatility_intraday--n-100000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 19476276.000 / 22913444.800 | 23711831.500 / 26380985.150 | 856988.500 / 1198121.550 | 1269994.500 / 2368722.650 | 121232.500 / 191699.300 | 49864367.000 / 58736408.650 | 29828.500 / 56362.500 | 0 | 0 | 0 |
| `lo_adjusted_sharpe_ratio--n-32` | `numpy` | `validated_public_reference` | 6459744.500 / 8378931.800 | N/A | N/A | N/A | N/A | N/A | 6459744.500 / 8378931.800 | 12824.500 / 13785.450 | 5.55112e-17 | 1.17299e-15 | 9.6837e-06 |
| `lo_adjusted_sharpe_ratio--n-32` | `jax_jit` | `compiled_device_numeric_core` | N/A | 53430447.000 / 119178998.000 | 158121857.500 / 356554880.350 | 856817.500 / 6702054.100 | 325081.000 / 700742.850 | 82501.500 / 344530.700 | 216060126.000 / 523053605.650 | 10428.500 / 16140.150 | 5.55112e-17 | 1.17299e-15 | 9.6837e-06 |
| `lo_adjusted_sharpe_ratio--n-252` | `numpy` | `validated_public_reference` | 7009321.000 / 9511338.300 | N/A | N/A | N/A | N/A | N/A | 7009321.000 / 9511338.300 | 11943.000 / 14690.100 | 5.55112e-17 | 3.32508e-16 | 3.13717e-06 |
| `lo_adjusted_sharpe_ratio--n-252` | `jax_jit` | `compiled_device_numeric_core` | N/A | 49021725.000 / 82567685.250 | 164046840.500 / 206235066.200 | 866274.500 / 977211.400 | 390026.500 / 536671.350 | 92945.000 / 139158.650 | 216203964.500 / 325175861.600 | 33539.500 / 43619.300 | 5.55112e-17 | 3.32508e-16 | 3.13717e-06 |
| `lo_adjusted_sharpe_ratio--n-1000` | `numpy` | `validated_public_reference` | 6667563.500 / 17862727.100 | N/A | N/A | N/A | N/A | N/A | 6667563.500 / 17862727.100 | 17467.000 / 18855.600 | 0 | 0 | 0 |
| `lo_adjusted_sharpe_ratio--n-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 48543294.000 / 78948917.000 | 164499084.000 / 278718127.850 | 901058.500 / 1149339.250 | 453320.000 / 854498.900 | 105013.000 / 231591.150 | 218160890.000 / 342594757.550 | 77947.000 / 117793.050 | 0 | 0 | 0 |
| `lo_adjusted_sharpe_ratio--n-10000` | `numpy` | `validated_public_reference` | 7364817.000 / 9305090.800 | N/A | N/A | N/A | N/A | N/A | 7364817.000 / 9305090.800 | 53308.000 / 68428.750 | 2.77556e-17 | 2.77418e-16 | 2.52209e-06 |
| `lo_adjusted_sharpe_ratio--n-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 50787366.000 / 62140426.600 | 172485526.500 / 204798280.950 | 816349.500 / 1080586.250 | 933183.500 / 1206710.850 | 114258.000 / 190739.650 | 231861140.000 / 259216948.150 | 484946.000 / 790628.350 | 2.77556e-17 | 2.77418e-16 | 2.52209e-06 |
| `lo_adjusted_sharpe_ratio--n-100000` | `numpy` | `validated_public_reference` | 9034547.000 / 18970376.200 | N/A | N/A | N/A | N/A | N/A | 9034547.000 / 18970376.200 | 1749442.000 / 2750046.600 | 2.77556e-17 | 2.72271e-16 | 2.47948e-06 |
| `lo_adjusted_sharpe_ratio--n-100000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 48181260.500 / 94899337.950 | 170028835.500 / 247795651.050 | 891254.500 / 1291055.000 | 6038230.500 / 7446942.700 | 212483.000 / 269402.500 | 231981867.500 / 360878450.050 | 4458248.500 / 8349240.650 | 2.77556e-17 | 2.72271e-16 | 2.47948e-06 |
| `kupiec_unconditional_coverage_test--n-32` | `numpy` | `validated_public_reference` | 6973956.500 / 8194528.850 | N/A | N/A | N/A | N/A | N/A | 6973956.500 / 8194528.850 | 13801.500 / 26020.200 | 4.84676e-27 | 8.28674e-16 | 4.84676e-15 |
| `kupiec_unconditional_coverage_test--n-32` | `jax_jit` | `compiled_device_numeric_core` | N/A | 30395914.000 / 49212859.350 | 89470349.000 / 127922676.250 | 863530.000 / 1206504.550 | 322444.500 / 438793.750 | 128884.500 / 195410.500 | 128324874.000 / 177505705.550 | 13329.000 / 22331.600 | 4.84676e-27 | 8.28674e-16 | 4.84676e-15 |
| `kupiec_unconditional_coverage_test--n-252` | `numpy` | `validated_public_reference` | 7724871.000 / 10628884.350 | N/A | N/A | N/A | N/A | N/A | 7724871.000 / 10628884.350 | 12377.000 / 23218.950 | 3.91732e-107 | 7.58538e-15 | 3.91732e-95 |
| `kupiec_unconditional_coverage_test--n-252` | `jax_jit` | `compiled_device_numeric_core` | N/A | 31135798.000 / 61685469.350 | 118847200.000 / 154633885.200 | 855714.000 / 1342312.700 | 338776.000 / 504399.100 | 135840.500 / 173584.850 | 158031499.500 / 232431803.400 | 15980.000 / 114936.900 | 3.91732e-107 | 7.58538e-15 | 3.91732e-95 |
| `kupiec_unconditional_coverage_test--n-1000` | `numpy` | `validated_public_reference` | 7208125.500 / 16252370.150 | N/A | N/A | N/A | N/A | N/A | 7208125.500 / 16252370.150 | 13137.000 / 23798.650 | 0 | 0 | 0 |
| `kupiec_unconditional_coverage_test--n-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 29604171.500 / 46496001.000 | 124981502.000 / 169266412.050 | 838029.000 / 1096843.050 | 360734.500 / 437627.850 | 129316.500 / 188477.800 | 164330714.500 / 222618281.250 | 28259.000 / 42853.700 | 0 | 0 | 0 |
| `kupiec_unconditional_coverage_test--n-10000` | `numpy` | `validated_public_reference` | 7504297.000 / 15488641.950 | N/A | N/A | N/A | N/A | N/A | 7504297.000 / 15488641.950 | 23450.000 / 34751.050 | 0 | 0 | 0 |
| `kupiec_unconditional_coverage_test--n-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 32338357.500 / 57807031.400 | 89708062.000 / 140389830.600 | 812890.000 / 1120643.250 | 923327.500 / 1485862.500 | 148115.000 / 267980.350 | 132037214.000 / 213045313.400 | 24995.000 / 34457.700 | 0 | 0 | 0 |
| `kupiec_unconditional_coverage_test--n-100000` | `numpy` | `validated_public_reference` | 9516741.500 / 20799881.300 | N/A | N/A | N/A | N/A | N/A | 9516741.500 / 20799881.300 | 830406.500 / 1369794.450 | 0 | 0 | 0 |
| `kupiec_unconditional_coverage_test--n-100000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 28647052.500 / 36871481.200 | 87142878.500 / 144672141.300 | 806481.500 / 1088756.550 | 1659716.000 / 1928283.800 | 158954.500 / 389130.950 | 124751526.000 / 187930009.800 | 100433.500 / 305120.450 | 0 | 0 | 0 |
| `christoffersen_independence_test--n-32` | `numpy` | `validated_public_reference` | 7012332.000 / 10252490.850 | N/A | N/A | N/A | N/A | N/A | 7012332.000 / 10252490.850 | 48007.000 / 84880.000 | 0 | 0 | 0 |
| `christoffersen_independence_test--n-32` | `jax_jit` | `compiled_device_numeric_core` | N/A | 32490660.000 / 66338723.400 | 191531818.500 / 266363676.150 | 807922.500 / 1359912.850 | 341720.500 / 850088.800 | 150432.500 / 282276.950 | 230059073.000 / 361157659.800 | 18399.000 / 26180.750 | 0 | 0 | 0 |
| `christoffersen_independence_test--n-252` | `numpy` | `validated_public_reference` | 6913785.000 / 10600963.300 | N/A | N/A | N/A | N/A | N/A | 6913785.000 / 10600963.300 | 22120.500 / 29515.950 | 0 | 0 | 0 |
| `christoffersen_independence_test--n-252` | `jax_jit` | `compiled_device_numeric_core` | N/A | 33694528.000 / 45360966.900 | 229902392.000 / 329224016.450 | 885868.500 / 1231251.150 | 423123.000 / 529827.000 | 167776.000 / 326809.500 | 272412196.000 / 375784552.650 | 35423.000 / 114760.350 | 0 | 0 | 0 |
| `christoffersen_independence_test--n-1000` | `numpy` | `validated_public_reference` | 7091678.500 / 11395799.650 | N/A | N/A | N/A | N/A | N/A | 7091678.500 / 11395799.650 | 25812.500 / 49715.850 | 2.27374e-13 | 1.21449e-13 | 2.00924e-05 |
| `christoffersen_independence_test--n-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 33721200.000 / 60460073.400 | 236871024.000 / 372939573.900 | 844305.500 / 1845626.350 | 412654.500 / 857953.950 | 162432.500 / 258819.050 | 289118379.500 / 428923038.250 | 45711.000 / 127809.550 | 2.27374e-13 | 1.21449e-13 | 2.00924e-05 |
| `christoffersen_independence_test--n-10000` | `numpy` | `validated_public_reference` | 7078027.500 / 11877735.600 | N/A | N/A | N/A | N/A | N/A | 7078027.500 / 11877735.600 | 103462.500 / 151857.400 | 3.63798e-12 | 1.83448e-12 | 3.21218e-05 |
| `christoffersen_independence_test--n-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 38470960.000 / 70155198.900 | 218725961.000 / 359685442.250 | 895465.500 / 1341629.250 | 2346652.000 / 3442680.800 | 218497.000 / 290643.500 | 272355562.500 / 439291199.700 | 68788.500 / 107655.900 | 3.63798e-12 | 1.83448e-12 | 3.21218e-05 |
| `christoffersen_independence_test--n-100000` | `numpy` | `validated_public_reference` | 9121192.000 / 11371686.300 | N/A | N/A | N/A | N/A | N/A | 9121192.000 / 11371686.300 | 1315650.000 / 1915414.600 | 0 | 0 | 0 |
| `christoffersen_independence_test--n-100000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 32289761.500 / 46881585.900 | 184369160.000 / 219796702.800 | 810997.000 / 936136.850 | 5904108.000 / 6736162.400 | 259524.000 / 310492.050 | 228703664.500 / 279103232.500 | 499316.000 / 685782.800 | 0 | 0 | 0 |
| `christoffersen_conditional_coverage_test--n-32` | `numpy` | `validated_public_reference` | 7435903.500 / 13024926.650 | N/A | N/A | N/A | N/A | N/A | 7435903.500 / 13024926.650 | 23019.500 / 29292.250 | 0 | 0 | 0 |
| `christoffersen_conditional_coverage_test--n-32` | `jax_jit` | `compiled_device_numeric_core` | N/A | 34675393.500 / 92647982.200 | 208696789.500 / 410465430.050 | 844825.000 / 1054219.200 | 358198.500 / 463130.300 | 163877.000 / 454320.200 | 249066336.500 / 533127693.800 | 20571.000 / 25729.900 | 0 | 0 | 0 |
| `christoffersen_conditional_coverage_test--n-252` | `numpy` | `validated_public_reference` | 7937338.000 / 11486063.850 | N/A | N/A | N/A | N/A | N/A | 7937338.000 / 11486063.850 | 22684.000 / 26446.750 | 0 | 0 | 0 |
| `christoffersen_conditional_coverage_test--n-252` | `jax_jit` | `compiled_device_numeric_core` | N/A | 37117261.000 / 75752002.200 | 245572469.500 / 333550556.250 | 822926.500 / 1593557.250 | 411382.000 / 622394.100 | 176572.500 / 300210.550 | 291997433.500 / 413945224.150 | 47035.500 / 129571.200 | 0 | 0 | 0 |
| `christoffersen_conditional_coverage_test--n-1000` | `numpy` | `validated_public_reference` | 7524308.000 / 11538664.500 | N/A | N/A | N/A | N/A | N/A | 7524308.000 / 11538664.500 | 29154.000 / 45377.200 | 2.27374e-13 | 2.00942e-15 | 2.00924e-05 |
| `christoffersen_conditional_coverage_test--n-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 32837646.500 / 44080818.800 | 248144105.500 / 346207300.100 | 806782.000 / 1058565.700 | 442257.500 / 544076.900 | 181962.500 / 301647.400 | 290643502.500 / 388549393.800 | 35879.500 / 65940.950 | 2.27374e-13 | 2.00942e-15 | 2.00924e-05 |
| `christoffersen_conditional_coverage_test--n-10000` | `numpy` | `validated_public_reference` | 6818030.500 / 12122871.850 | N/A | N/A | N/A | N/A | N/A | 6818030.500 / 12122871.850 | 61851.000 / 83258.400 | 3.63798e-12 | 3.21221e-15 | 3.21218e-05 |
| `christoffersen_conditional_coverage_test--n-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 35071080.500 / 54991208.300 | 218148658.000 / 423407715.500 | 819247.500 / 1050179.400 | 2263637.000 / 11230040.150 | 222813.000 / 452560.600 | 261965354.000 / 476410056.700 | 55157.000 / 71867.050 | 3.63798e-12 | 3.21221e-15 | 3.21218e-05 |
| `christoffersen_conditional_coverage_test--n-100000` | `numpy` | `validated_public_reference` | 8422425.500 / 11033020.900 | N/A | N/A | N/A | N/A | N/A | 8422425.500 / 11033020.900 | 1097178.000 / 1852207.800 | 0 | 0 | 0 |
| `christoffersen_conditional_coverage_test--n-100000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 35933561.000 / 47001866.750 | 221289951.000 / 350080922.550 | 817254.000 / 1810722.200 | 5984487.500 / 12096536.350 | 310936.000 / 1310013.200 | 265598736.500 / 413924848.350 | 525137.500 / 958248.000 | 0 | 0 | 0 |
| `historical_expected_shortfall--paths-100` | `numpy` | `validated_public_reference` | 11053278.000 / 21128625.000 | N/A | N/A | N/A | N/A | N/A | 11053278.000 / 21128625.000 | 2696050.000 / 4358171.800 | 1.38778e-17 | 2.94462e-16 | 2.42919e-06 |
| `historical_expected_shortfall--paths-100` | `jax_jit` | `compiled_device_numeric_core` | N/A | 74505574.000 / 105231758.650 | 107315231.500 / 123150240.400 | 767794.500 / 1177512.150 | 4714662.000 / 6868956.100 | 162354.500 / 324853.650 | 194035134.500 / 227115237.850 | 2383179.000 / 3203789.450 | 1.38778e-17 | 2.94462e-16 | 2.42919e-06 |
| `historical_expected_shortfall--paths-1000` | `numpy` | `validated_public_reference` | 37681464.500 / 62751139.450 | N/A | N/A | N/A | N/A | N/A | 37681464.500 / 62751139.450 | 35626545.500 / 48676749.700 | 1.38778e-17 | 3.17885e-16 | 2.58641e-06 |
| `historical_expected_shortfall--paths-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 76154431.500 / 134491584.100 | 117331383.500 / 178363750.250 | 842946.000 / 1549747.600 | 29465692.000 / 55187144.800 | 242825.500 / 478452.550 | 227761655.500 / 364664807.850 | 24951748.000 / 36630705.550 | 1.38778e-17 | 3.17885e-16 | 2.58641e-06 |
| `historical_expected_shortfall--paths-10000` | `numpy` | `validated_public_reference` | 372165719.000 / 506793621.450 | N/A | N/A | N/A | N/A | N/A | 372165719.000 / 506793621.450 | 335471897.000 / 452964243.850 | 1.38778e-17 | 3.49491e-16 | 2.79183e-06 |
| `historical_expected_shortfall--paths-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 74663687.500 / 107301626.500 | 124399659.500 / 213024748.400 | 886347.500 / 1117277.800 | 271761543.500 / 536687261.350 | 240494.500 / 272016.150 | 485188980.000 / 905530700.150 | 278413044.500 / 476975681.750 | 1.38778e-17 | 3.49491e-16 | 2.79183e-06 |
| `realized_variance--paths-100` | `numpy` | `validated_public_reference` | 8280231.500 / 10526437.200 | N/A | N/A | N/A | N/A | N/A | 8280231.500 / 10526437.200 | 490832.000 / 902379.200 | 2.77556e-17 | 3.05894e-16 | 2.75528e-06 |
| `realized_variance--paths-100` | `jax_jit` | `compiled_device_numeric_core` | N/A | 19784324.500 / 48595441.800 | 8451923.000 / 12529488.300 | 701279.000 / 973663.850 | 959040.000 / 3510656.000 | 106875.500 / 254734.900 | 36228192.000 / 76050236.450 | 22984.000 / 36745.750 | 2.77556e-17 | 3.05894e-16 | 2.75528e-06 |
| `realized_variance--paths-1000` | `numpy` | `validated_public_reference` | 12875493.500 / 24732901.300 | N/A | N/A | N/A | N/A | N/A | 12875493.500 / 24732901.300 | 5383149.500 / 8167711.450 | 2.77556e-17 | 3.1566e-16 | 2.83426e-06 |
| `realized_variance--paths-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 20785451.000 / 35268340.900 | 8506277.500 / 14023474.050 | 841750.000 / 1079778.650 | 1032323.000 / 3550483.600 | 122575.000 / 248144.750 | 36535077.500 / 58915096.650 | 62375.000 / 110408.950 | 2.77556e-17 | 3.1566e-16 | 2.83426e-06 |
| `realized_variance--paths-10000` | `numpy` | `validated_public_reference` | 70353603.500 / 149810670.150 | N/A | N/A | N/A | N/A | N/A | 70353603.500 / 149810670.150 | 54155776.000 / 87681644.550 | 4.16334e-17 | 4.04784e-16 | 3.68916e-06 |
| `realized_variance--paths-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 20495035.000 / 27707348.700 | 7998149.500 / 9750081.200 | 830941.500 / 1308627.450 | 3258728.000 / 3551614.700 | 197693.500 / 390141.550 | 40412138.500 / 51775120.600 | 908226.000 / 1052508.700 | 4.16334e-17 | 4.04784e-16 | 3.68916e-06 |
| `realized_volatility_intraday--paths-100` | `numpy` | `validated_public_reference` | 8377372.000 / 16679062.650 | N/A | N/A | N/A | N/A | N/A | 8377372.000 / 16679062.650 | 473904.500 / 860643.600 | 5.55112e-17 | 1.91422e-16 | 1.85041e-06 |
| `realized_volatility_intraday--paths-100` | `jax_jit` | `compiled_device_numeric_core` | N/A | 21961672.000 / 38588213.300 | 31093156.500 / 40787136.200 | 820430.000 / 983642.200 | 1008968.000 / 1592058.150 | 125119.500 / 293446.900 | 59257950.000 / 91981397.250 | 25237.000 / 81521.250 | 5.55112e-17 | 1.91422e-16 | 1.85041e-06 |
| `realized_volatility_intraday--paths-1000` | `numpy` | `validated_public_reference` | 13442265.500 / 18388123.150 | N/A | N/A | N/A | N/A | N/A | 13442265.500 / 18388123.150 | 5139735.000 / 9354412.250 | 5.55112e-17 | 2.00208e-16 | 1.93238e-06 |
| `realized_volatility_intraday--paths-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 20323531.000 / 31985343.350 | 27145926.000 / 52212686.700 | 791397.500 / 1325718.100 | 1011943.500 / 1525549.550 | 115521.500 / 222042.350 | 53273701.500 / 94614416.750 | 58257.000 / 133859.000 | 5.55112e-17 | 2.00208e-16 | 1.93238e-06 |
| `realized_volatility_intraday--paths-10000` | `numpy` | `validated_public_reference` | 70586093.000 / 85322062.400 | N/A | N/A | N/A | N/A | N/A | 70586093.000 / 85322062.400 | 60319252.500 / 104997797.300 | 5.55112e-17 | 2.02307e-16 | 1.95193e-06 |
| `realized_volatility_intraday--paths-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 20604876.000 / 34968501.250 | 8657073.000 / 11924899.200 | 912747.500 / 1393874.500 | 2302143.000 / 3584158.650 | 169440.500 / 254361.000 | 41294636.500 / 63857801.400 | 964142.500 / 1613168.300 | 5.55112e-17 | 2.02307e-16 | 1.95193e-06 |
| `lo_adjusted_sharpe_ratio--paths-100` | `numpy` | `validated_public_reference` | 8517176.500 / 10994277.400 | N/A | N/A | N/A | N/A | N/A | 8517176.500 / 10994277.400 | 1409380.500 / 2233646.700 | 1.66533e-16 | 1.62672e-15 | 1.06953e-05 |
| `lo_adjusted_sharpe_ratio--paths-100` | `jax_jit` | `compiled_device_numeric_core` | N/A | 67626388.000 / 80194913.500 | 324005935.000 / 447202791.100 | 778655.000 / 1471684.050 | 1187636.500 / 1716052.850 | 129383.500 / 300778.600 | 400111373.500 / 517973038.100 | 380111.000 / 620659.250 | 1.66533e-16 | 1.62672e-15 | 1.06953e-05 |
| `lo_adjusted_sharpe_ratio--paths-1000` | `numpy` | `validated_public_reference` | 26404374.000 / 61583715.450 | N/A | N/A | N/A | N/A | N/A | 26404374.000 / 61583715.450 | 16169625.000 / 22373548.500 | 2.77556e-16 | 8.50352e-14 | 3.20814e-05 |
| `lo_adjusted_sharpe_ratio--paths-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 64276999.500 / 97855152.550 | 302808619.000 / 419042768.100 | 816843.000 / 1265823.900 | 6700814.000 / 9579788.150 | 219390.500 / 270479.250 | 387728234.500 / 496185976.450 | 4067768.500 / 13283421.600 | 2.77556e-16 | 8.50352e-14 | 3.20814e-05 |
| `lo_adjusted_sharpe_ratio--paths-10000` | `numpy` | `validated_public_reference` | 170202062.000 / 289823187.150 | N/A | N/A | N/A | N/A | N/A | 170202062.000 / 289823187.150 | 156951568.000 / 248000193.600 | 3.33067e-16 | 8.09372e-13 | 8.33422e-05 |
| `lo_adjusted_sharpe_ratio--paths-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 62220448.000 / 87134949.400 | 303612725.000 / 340514305.500 | 940859.500 / 1334409.500 | 120787508.000 / 169385254.350 | 256632.000 / 314789.800 | 504471707.500 / 603787565.700 | 137500399.500 / 246980940.250 | 3.33067e-16 | 8.09372e-13 | 8.33422e-05 |
| `kupiec_unconditional_coverage_test--paths-100` | `numpy` | `validated_public_reference` | 9867018.000 / 16838454.550 | N/A | N/A | N/A | N/A | N/A | 9867018.000 / 16838454.550 | 1240554.500 / 2231600.850 | 3.91732e-107 | 7.58538e-15 | 3.91732e-95 |
| `kupiec_unconditional_coverage_test--paths-100` | `jax_jit` | `compiled_device_numeric_core` | N/A | 41016876.500 / 69752391.350 | 104116751.500 / 205701058.950 | 837419.000 / 1302600.100 | 1279916.500 / 2035076.550 | 176913.000 / 275994.500 | 153390191.000 / 299825508.750 | 42610.500 / 58593.600 | 3.91732e-107 | 7.58538e-15 | 3.91732e-95 |
| `kupiec_unconditional_coverage_test--paths-1000` | `numpy` | `validated_public_reference` | 21728004.500 / 30362556.850 | N/A | N/A | N/A | N/A | N/A | 21728004.500 / 30362556.850 | 16795197.000 / 23790373.900 | 3.91732e-107 | 7.58538e-15 | 3.91732e-95 |
| `kupiec_unconditional_coverage_test--paths-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 36742768.500 / 45346818.400 | 97160359.500 / 133961734.750 | 832784.500 / 1070171.250 | 2239916.000 / 2481579.550 | 200766.000 / 514833.650 | 144045458.500 / 184753730.100 | 357606.500 / 550137.000 | 3.91732e-107 | 7.58538e-15 | 3.91732e-95 |
| `kupiec_unconditional_coverage_test--paths-10000` | `numpy` | `validated_public_reference` | 220342700.000 / 315522496.600 | N/A | N/A | N/A | N/A | N/A | 220342700.000 / 315522496.600 | 209318078.500 / 429301018.900 | 3.91732e-107 | 7.58538e-15 | 3.91732e-95 |
| `kupiec_unconditional_coverage_test--paths-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 40526147.500 / 76573299.200 | 109622123.000 / 239353673.350 | 14369242.500 / 24853346.250 | 16440342.000 / 27153463.650 | 524596.500 / 776692.600 | 201441354.000 / 390453172.400 | 6773154.500 / 8689913.900 | 3.91732e-107 | 7.58538e-15 | 3.91732e-95 |
| `christoffersen_independence_test--paths-100` | `numpy` | `validated_public_reference` | 10086581.000 / 20425925.200 | N/A | N/A | N/A | N/A | N/A | 10086581.000 / 20425925.200 | 2371128.000 / 3638083.500 | 5.68434e-14 | 3.13281e-14 | 2.03174e-05 |
| `christoffersen_independence_test--paths-100` | `jax_jit` | `compiled_device_numeric_core` | N/A | 40978051.500 / 58120781.850 | 234329472.500 / 298108619.900 | 728841.000 / 1079483.550 | 3569863.000 / 4349391.200 | 257130.500 / 325907.400 | 281244867.000 / 357695942.900 | 121878.000 / 167932.600 | 5.68434e-14 | 3.13281e-14 | 2.03174e-05 |
| `christoffersen_independence_test--paths-1000` | `numpy` | `validated_public_reference` | 35250773.000 / 41960650.000 | N/A | N/A | N/A | N/A | N/A | 35250773.000 / 41960650.000 | 28810470.500 / 50886618.500 | 5.68434e-14 | 3.13281e-14 | 2.03174e-05 |
| `christoffersen_independence_test--paths-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 44177191.500 / 77135906.500 | 230901460.500 / 402043595.550 | 850189.000 / 1284166.050 | 8572643.500 / 12393318.050 | 314147.000 / 410271.650 | 290537176.000 / 472487720.950 | 1785462.000 / 2452841.250 | 5.68434e-14 | 3.13281e-14 | 2.03174e-05 |
| `christoffersen_independence_test--paths-10000` | `numpy` | `validated_public_reference` | 464744962.500 / 735598484.750 | N/A | N/A | N/A | N/A | N/A | 464744962.500 / 735598484.750 | 411652603.000 / 580232952.800 | 5.68434e-14 | 3.13281e-14 | 2.03174e-05 |
| `christoffersen_independence_test--paths-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 40895831.000 / 98937141.150 | 250662281.500 / 368892780.700 | 13715161.000 / 16761701.850 | 81999633.000 / 103299600.000 | 624232.000 / 716326.400 | 413417002.000 / 585949476.450 | 80686365.500 / 110259158.800 | 5.68434e-14 | 3.13281e-14 | 2.03174e-05 |
| `christoffersen_conditional_coverage_test--paths-100` | `numpy` | `validated_public_reference` | 11956956.000 / 22401234.950 | N/A | N/A | N/A | N/A | N/A | 11956956.000 / 22401234.950 | 4232529.500 / 8512776.800 | 5.68434e-14 | 2.84171e-14 | 2.03174e-05 |
| `christoffersen_conditional_coverage_test--paths-100` | `jax_jit` | `compiled_device_numeric_core` | N/A | 49101036.500 / 77550574.700 | 272094838.000 / 403005769.250 | 781247.500 / 1107544.500 | 3707013.000 / 7069747.550 | 257421.500 / 378687.700 | 332046265.500 / 468986314.200 | 119029.500 / 167819.500 | 5.68434e-14 | 2.84171e-14 | 2.03174e-05 |
| `christoffersen_conditional_coverage_test--paths-1000` | `numpy` | `validated_public_reference` | 41168272.500 / 66733336.750 | N/A | N/A | N/A | N/A | N/A | 41168272.500 / 66733336.750 | 28052646.000 / 61197667.700 | 5.68434e-14 | 2.84171e-14 | 2.03174e-05 |
| `christoffersen_conditional_coverage_test--paths-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 45111076.000 / 74480247.500 | 243196440.500 / 316532685.450 | 804568.500 / 975615.750 | 8280754.000 / 9906946.800 | 383778.000 / 710778.000 | 306057008.500 / 418856086.800 | 1891525.000 / 2514850.550 | 5.68434e-14 | 2.84171e-14 | 2.03174e-05 |
| `christoffersen_conditional_coverage_test--paths-10000` | `numpy` | `validated_public_reference` | 456934028.500 / 633013995.650 | N/A | N/A | N/A | N/A | N/A | 456934028.500 / 633013995.650 | 447808058.500 / 716481673.300 | 5.68434e-14 | 2.84171e-14 | 2.03174e-05 |
| `christoffersen_conditional_coverage_test--paths-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 45538516.500 / 81011135.600 | 249230049.500 / 340554800.100 | 14267259.000 / 21185884.400 | 83722155.500 / 139733135.500 | 745779.000 / 1188575.700 | 430893121.500 / 617966701.300 | 79718029.000 / 110974832.150 | 5.68434e-14 | 2.84171e-14 | 2.03174e-05 |
| `probabilistic_sharpe_ratio--paths-100` | `numpy` | `validated_public_reference` | 7687963.500 / 11495444.650 | N/A | N/A | N/A | N/A | N/A | 7687963.500 / 11495444.650 | 419186.500 / 494938.950 | 1.11022e-16 | 1.6182e-16 | 1.59496e-06 |
| `probabilistic_sharpe_ratio--paths-100` | `jax_jit` | `compiled_device_numeric_core` | N/A | 30254151.500 / 39992490.600 | 28222110.500 / 37756824.200 | 760573.000 / 1211609.350 | 350225.500 / 509680.900 | 99028.500 / 137876.550 | 64866515.500 / 84758356.400 | 18749.000 / 32910.050 | 1.11022e-16 | 1.6182e-16 | 1.59496e-06 |
| `probabilistic_sharpe_ratio--paths-1000` | `numpy` | `validated_public_reference` | 11307642.500 / 15814591.350 | N/A | N/A | N/A | N/A | N/A | 11307642.500 / 15814591.350 | 3802409.500 / 4843151.700 | 2.22045e-16 | 1.17458e-15 | 9.69451e-06 |
| `probabilistic_sharpe_ratio--paths-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 29658238.500 / 61824180.150 | 30130734.500 / 37574658.600 | 781704.000 / 1214934.500 | 363311.500 / 708496.100 | 96381.000 / 232327.950 | 67726682.500 / 119354204.450 | 49807.000 / 109231.250 | 2.22045e-16 | 1.17458e-15 | 9.69451e-06 |
| `probabilistic_sharpe_ratio--paths-10000` | `numpy` | `validated_public_reference` | 52270189.500 / 81204304.850 | N/A | N/A | N/A | N/A | N/A | 52270189.500 / 81204304.850 | 43205563.000 / 53820930.300 | 2.22045e-16 | 8.8159e-15 | 3.40628e-05 |
| `probabilistic_sharpe_ratio--paths-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 28785124.000 / 40531645.400 | 28934759.500 / 43247993.350 | 854534.000 / 1862151.000 | 679777.000 / 1311816.700 | 107281.500 / 206840.950 | 66035195.500 / 88114982.250 | 265266.000 / 395189.700 | 2.22045e-16 | 8.8159e-15 | 3.40628e-05 |
| `deflated_sharpe_ratio--paths-100` | `numpy` | `validated_public_reference` | 72290119.000 / 100888781.300 | N/A | N/A | N/A | N/A | N/A | 72290119.000 / 100888781.300 | 2401468.500 / 3861082.400 | 2.22045e-16 | 6.32951e-15 | 2.95741e-05 |
| `deflated_sharpe_ratio--paths-100` | `jax_jit` | `compiled_device_numeric_core` | N/A | 68315111.000 / 116867476.900 | 188923606.500 / 240845246.550 | 935164.500 / 1362341.150 | 380778.500 / 797718.500 | 88227.000 / 189230.700 | 268978649.000 / 363160940.450 | 55746.500 / 204442.300 | 2.22045e-16 | 6.32951e-15 | 2.95741e-05 |
| `deflated_sharpe_ratio--paths-1000` | `numpy` | `validated_public_reference` | 91170023.000 / 129837681.650 | N/A | N/A | N/A | N/A | N/A | 91170023.000 / 129837681.650 | 24080362.500 / 56833647.350 | 2.22045e-16 | 4.5514e-14 | 4.94767e-05 |
| `deflated_sharpe_ratio--paths-1000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 69552929.500 / 130680136.450 | 198791793.500 / 309279168.900 | 785426.500 / 1190967.600 | 657706.000 / 1368886.800 | 103795.500 / 187427.700 | 278835710.000 / 405773410.150 | 288714.500 / 379475.500 | 2.22045e-16 | 4.5514e-14 | 4.94767e-05 |
| `deflated_sharpe_ratio--paths-10000` | `numpy` | `validated_public_reference` | 318279640.500 / 537522138.950 | N/A | N/A | N/A | N/A | N/A | 318279640.500 / 537522138.950 | 241422686.000 / 340778698.550 | 2.22045e-16 | 6.63316e-14 | 5.12243e-05 |
| `deflated_sharpe_ratio--paths-10000` | `jax_jit` | `compiled_device_numeric_core` | N/A | 68176374.500 / 122072345.750 | 201020670.000 / 263246055.750 | 817669.000 / 1034583.550 | 3260648.500 / 3979549.200 | 137043.500 / 246991.800 | 288154136.500 / 364352459.050 | 2821616.500 / 3444020.550 | 2.22045e-16 | 6.63316e-14 | 5.12243e-05 |

## Throughput / 처리량

| Case | Impl | calls/s | observations/s | paths/s | path-observations/s | evaluations/s |
|---|---|---:|---:|---:|---:|---:|
| `historical_expected_shortfall--n-32` | `numpy` | 102010 | 3.26431e+06 | N/A | N/A | N/A |
| `historical_expected_shortfall--n-32` | `jax_jit` | 48680.8 | 1.55778e+06 | N/A | N/A | N/A |
| `historical_expected_shortfall--n-252` | `numpy` | 40202.6 | 1.01311e+07 | N/A | N/A | N/A |
| `historical_expected_shortfall--n-252` | `jax_jit` | 25287 | 6.37233e+06 | N/A | N/A | N/A |
| `historical_expected_shortfall--n-1000` | `numpy` | 13031.7 | 1.30317e+07 | N/A | N/A | N/A |
| `historical_expected_shortfall--n-1000` | `jax_jit` | 6967.94 | 6.96794e+06 | N/A | N/A | N/A |
| `historical_expected_shortfall--n-10000` | `numpy` | 1255.04 | 1.25504e+07 | N/A | N/A | N/A |
| `historical_expected_shortfall--n-10000` | `jax_jit` | 580.847 | 5.80847e+06 | N/A | N/A | N/A |
| `historical_expected_shortfall--n-100000` | `numpy` | 94.7092 | 9.47092e+06 | N/A | N/A | N/A |
| `historical_expected_shortfall--n-100000` | `jax_jit` | 55.5656 | 5.55656e+06 | N/A | N/A | N/A |
| `realized_variance--n-32` | `numpy` | 271150 | 8.67679e+06 | N/A | N/A | N/A |
| `realized_variance--n-32` | `jax_jit` | 105513 | 3.37642e+06 | N/A | N/A | N/A |
| `realized_variance--n-252` | `numpy` | 260281 | 6.55908e+07 | N/A | N/A | N/A |
| `realized_variance--n-252` | `jax_jit` | 87800.2 | 2.21256e+07 | N/A | N/A | N/A |
| `realized_variance--n-1000` | `numpy` | 213470 | 2.1347e+08 | N/A | N/A | N/A |
| `realized_variance--n-1000` | `jax_jit` | 57957.6 | 5.79576e+07 | N/A | N/A | N/A |
| `realized_variance--n-10000` | `numpy` | 90975.3 | 9.09753e+08 | N/A | N/A | N/A |
| `realized_variance--n-10000` | `jax_jit` | 52228.9 | 5.22289e+08 | N/A | N/A | N/A |
| `realized_variance--n-100000` | `numpy` | 1710.58 | 1.71058e+08 | N/A | N/A | N/A |
| `realized_variance--n-100000` | `jax_jit` | 30169.1 | 3.01691e+09 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-32` | `numpy` | 187336 | 5.99475e+06 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-32` | `jax_jit` | 102291 | 3.27332e+06 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-252` | `numpy` | 198157 | 4.99356e+07 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-252` | `jax_jit` | 123403 | 3.10977e+07 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-1000` | `numpy` | 209710 | 2.0971e+08 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-1000` | `jax_jit` | 63251.1 | 6.32511e+07 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-10000` | `numpy` | 86979.2 | 8.69792e+08 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-10000` | `jax_jit` | 47882.4 | 4.78824e+08 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-100000` | `numpy` | 1435.32 | 1.43532e+08 | N/A | N/A | N/A |
| `realized_volatility_intraday--n-100000` | `jax_jit` | 33525 | 3.3525e+09 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-32` | `numpy` | 77975.7 | 2.49522e+06 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-32` | `jax_jit` | 95891.1 | 3.06851e+06 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-252` | `numpy` | 83731.1 | 2.11002e+07 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-252` | `jax_jit` | 29815.6 | 7.51353e+06 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-1000` | `numpy` | 57250.8 | 5.72508e+07 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-1000` | `jax_jit` | 12829.2 | 1.28292e+07 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-10000` | `numpy` | 18758.9 | 1.87589e+08 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-10000` | `jax_jit` | 2062.09 | 2.06209e+07 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-100000` | `numpy` | 571.611 | 5.71611e+07 | N/A | N/A | N/A |
| `lo_adjusted_sharpe_ratio--n-100000` | `jax_jit` | 224.303 | 2.24303e+07 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-32` | `numpy` | 72455.9 | 2.31859e+06 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-32` | `jax_jit` | 75024.4 | 2.40078e+06 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-252` | `numpy` | 80795 | 2.03603e+07 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-252` | `jax_jit` | 62578.2 | 1.57697e+07 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-1000` | `numpy` | 76120.9 | 7.61209e+07 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-1000` | `jax_jit` | 35387 | 3.5387e+07 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-10000` | `numpy` | 42643.9 | 4.26439e+08 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-10000` | `jax_jit` | 40008 | 4.0008e+08 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-100000` | `numpy` | 1204.23 | 1.20423e+08 | N/A | N/A | N/A |
| `kupiec_unconditional_coverage_test--n-100000` | `jax_jit` | 9956.84 | 9.95684e+08 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-32` | `numpy` | 20830.3 | 666569 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-32` | `jax_jit` | 54350.8 | 1.73922e+06 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-252` | `numpy` | 45206.9 | 1.13921e+07 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-252` | `jax_jit` | 28230.2 | 7.11402e+06 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-1000` | `numpy` | 38740.9 | 3.87409e+07 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-1000` | `jax_jit` | 21876.6 | 2.18766e+07 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-10000` | `numpy` | 9665.34 | 9.66534e+07 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-10000` | `jax_jit` | 14537.3 | 1.45373e+08 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-100000` | `numpy` | 760.081 | 7.60081e+07 | N/A | N/A | N/A |
| `christoffersen_independence_test--n-100000` | `jax_jit` | 2002.74 | 2.00274e+08 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-32` | `numpy` | 43441.4 | 1.39013e+06 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-32` | `jax_jit` | 48612.1 | 1.55559e+06 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-252` | `numpy` | 44083.9 | 1.11092e+07 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-252` | `jax_jit` | 21260.5 | 5.35766e+06 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-1000` | `numpy` | 34300.6 | 3.43006e+07 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-1000` | `jax_jit` | 27871.1 | 2.78711e+07 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-10000` | `numpy` | 16167.9 | 1.61679e+08 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-10000` | `jax_jit` | 18130.1 | 1.81301e+08 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-100000` | `numpy` | 911.429 | 9.11429e+07 | N/A | N/A | N/A |
| `christoffersen_conditional_coverage_test--n-100000` | `jax_jit` | 1904.26 | 1.90426e+08 | N/A | N/A | N/A |
| `historical_expected_shortfall--paths-100` | `numpy` | 370.913 | N/A | 37091.3 | 9.34701e+06 | N/A |
| `historical_expected_shortfall--paths-100` | `jax_jit` | 419.608 | N/A | 41960.8 | 1.05741e+07 | N/A |
| `historical_expected_shortfall--paths-1000` | `numpy` | 28.069 | N/A | 28069 | 7.07338e+06 | N/A |
| `historical_expected_shortfall--paths-1000` | `jax_jit` | 40.0774 | N/A | 40077.4 | 1.00995e+07 | N/A |
| `historical_expected_shortfall--paths-10000` | `numpy` | 2.98088 | N/A | 29808.8 | 7.51181e+06 | N/A |
| `historical_expected_shortfall--paths-10000` | `jax_jit` | 3.59179 | N/A | 35917.9 | 9.0513e+06 | N/A |
| `realized_variance--paths-100` | `numpy` | 2037.36 | N/A | 203736 | 5.13414e+07 | N/A |
| `realized_variance--paths-100` | `jax_jit` | 43508.5 | N/A | 4.35085e+06 | 1.09641e+09 | N/A |
| `realized_variance--paths-1000` | `numpy` | 185.765 | N/A | 185765 | 4.68127e+07 | N/A |
| `realized_variance--paths-1000` | `jax_jit` | 16032.1 | N/A | 1.60321e+07 | 4.04008e+09 | N/A |
| `realized_variance--paths-10000` | `numpy` | 18.4653 | N/A | 184653 | 4.65324e+07 | N/A |
| `realized_variance--paths-10000` | `jax_jit` | 1101.05 | N/A | 1.10105e+07 | 2.77464e+09 | N/A |
| `realized_volatility_intraday--paths-100` | `numpy` | 2110.13 | N/A | 211013 | 5.31753e+07 | N/A |
| `realized_volatility_intraday--paths-100` | `jax_jit` | 39624.4 | N/A | 3.96244e+06 | 9.98534e+08 | N/A |
| `realized_volatility_intraday--paths-1000` | `numpy` | 194.563 | N/A | 194563 | 4.90298e+07 | N/A |
| `realized_volatility_intraday--paths-1000` | `jax_jit` | 17165.3 | N/A | 1.71653e+07 | 4.32566e+09 | N/A |
| `realized_volatility_intraday--paths-10000` | `numpy` | 16.5785 | N/A | 165785 | 4.17777e+07 | N/A |
| `realized_volatility_intraday--paths-10000` | `jax_jit` | 1037.19 | N/A | 1.03719e+07 | 2.61372e+09 | N/A |
| `lo_adjusted_sharpe_ratio--paths-100` | `numpy` | 709.532 | N/A | 70953.2 | 1.78802e+07 | N/A |
| `lo_adjusted_sharpe_ratio--paths-100` | `jax_jit` | 2630.81 | N/A | 263081 | 6.62964e+07 | N/A |
| `lo_adjusted_sharpe_ratio--paths-1000` | `numpy` | 61.8444 | N/A | 61844.4 | 1.55848e+07 | N/A |
| `lo_adjusted_sharpe_ratio--paths-1000` | `jax_jit` | 245.835 | N/A | 245835 | 6.19504e+07 | N/A |
| `lo_adjusted_sharpe_ratio--paths-10000` | `numpy` | 6.37139 | N/A | 63713.9 | 1.60559e+07 | N/A |
| `lo_adjusted_sharpe_ratio--paths-10000` | `jax_jit` | 7.27271 | N/A | 72727.1 | 1.83272e+07 | N/A |
| `kupiec_unconditional_coverage_test--paths-100` | `numpy` | 806.091 | N/A | 80609.1 | 2.03135e+07 | N/A |
| `kupiec_unconditional_coverage_test--paths-100` | `jax_jit` | 23468.4 | N/A | 2.34684e+06 | 5.91404e+08 | N/A |
| `kupiec_unconditional_coverage_test--paths-1000` | `numpy` | 59.5408 | N/A | 59540.8 | 1.50043e+07 | N/A |
| `kupiec_unconditional_coverage_test--paths-1000` | `jax_jit` | 2796.37 | N/A | 2.79637e+06 | 7.04685e+08 | N/A |
| `kupiec_unconditional_coverage_test--paths-10000` | `numpy` | 4.77742 | N/A | 47774.2 | 1.20391e+07 | N/A |
| `kupiec_unconditional_coverage_test--paths-10000` | `jax_jit` | 147.642 | N/A | 1.47642e+06 | 3.72057e+08 | N/A |
| `christoffersen_independence_test--paths-100` | `numpy` | 421.74 | N/A | 42174 | 1.06279e+07 | N/A |
| `christoffersen_independence_test--paths-100` | `jax_jit` | 8204.93 | N/A | 820493 | 2.06764e+08 | N/A |
| `christoffersen_independence_test--paths-1000` | `numpy` | 34.7096 | N/A | 34709.6 | 8.74682e+06 | N/A |
| `christoffersen_independence_test--paths-1000` | `jax_jit` | 560.079 | N/A | 560079 | 1.4114e+08 | N/A |
| `christoffersen_independence_test--paths-10000` | `numpy` | 2.42923 | N/A | 24292.3 | 6.12167e+06 | N/A |
| `christoffersen_independence_test--paths-10000` | `jax_jit` | 12.3937 | N/A | 123937 | 3.1232e+07 | N/A |
| `christoffersen_conditional_coverage_test--paths-100` | `numpy` | 236.265 | N/A | 23626.5 | 5.95389e+06 | N/A |
| `christoffersen_conditional_coverage_test--paths-100` | `jax_jit` | 8401.28 | N/A | 840128 | 2.11712e+08 | N/A |
| `christoffersen_conditional_coverage_test--paths-1000` | `numpy` | 35.6473 | N/A | 35647.3 | 8.98311e+06 | N/A |
| `christoffersen_conditional_coverage_test--paths-1000` | `jax_jit` | 528.674 | N/A | 528674 | 1.33226e+08 | N/A |
| `christoffersen_conditional_coverage_test--paths-10000` | `numpy` | 2.2331 | N/A | 22331 | 5.62741e+06 | N/A |
| `christoffersen_conditional_coverage_test--paths-10000` | `jax_jit` | 12.5442 | N/A | 125442 | 3.16114e+07 | N/A |
| `probabilistic_sharpe_ratio--paths-100` | `numpy` | 2385.57 | N/A | N/A | N/A | 238557 |
| `probabilistic_sharpe_ratio--paths-100` | `jax_jit` | 53336.2 | N/A | N/A | N/A | 5.33362e+06 |
| `probabilistic_sharpe_ratio--paths-1000` | `numpy` | 262.991 | N/A | N/A | N/A | 262991 |
| `probabilistic_sharpe_ratio--paths-1000` | `jax_jit` | 20077.5 | N/A | N/A | N/A | 2.00775e+07 |
| `probabilistic_sharpe_ratio--paths-10000` | `numpy` | 23.1452 | N/A | N/A | N/A | 231452 |
| `probabilistic_sharpe_ratio--paths-10000` | `jax_jit` | 3769.8 | N/A | N/A | N/A | 3.7698e+07 |
| `deflated_sharpe_ratio--paths-100` | `numpy` | 416.412 | N/A | N/A | N/A | 41641.2 |
| `deflated_sharpe_ratio--paths-100` | `jax_jit` | 17938.3 | N/A | N/A | N/A | 1.79383e+06 |
| `deflated_sharpe_ratio--paths-1000` | `numpy` | 41.5276 | N/A | N/A | N/A | 41527.6 |
| `deflated_sharpe_ratio--paths-1000` | `jax_jit` | 3463.63 | N/A | N/A | N/A | 3.46363e+06 |
| `deflated_sharpe_ratio--paths-10000` | `numpy` | 4.14211 | N/A | N/A | N/A | 41421.1 |
| `deflated_sharpe_ratio--paths-10000` | `jax_jit` | 354.407 | N/A | N/A | N/A | 3.54407e+06 |

## Memory and chunking / 메모리와 청킹

RSS is diagnostic process evidence and is not added to the bounded input/temporary/output data-working-set ledger.

| Case | Impl | Estimated/cap bytes | NumPy traced bytes | RSS baseline/peak/delta | Chunk size/count/last/padding | Estimator |
|---|---|---:|---:|---:|---:|---|
| `historical_expected_shortfall--n-32` | `numpy` | 68360 / 536870912 | 3336 | 31203328 / 182157312 / 150953984 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `historical_expected_shortfall--n-32` | `jax_jit` | 3608 / 536870912 | N/A | 31203328 / 226734080 / 195530752 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `historical_expected_shortfall--n-252` | `numpy` | 87720 / 536870912 | 13286 | 31227904 / 182550528 / 151322624 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `historical_expected_shortfall--n-252` | `jax_jit` | 28248 / 536870912 | N/A | 31363072 / 233246720 / 201883648 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `historical_expected_shortfall--n-1000` | `numpy` | 153544 / 536870912 | 49190 | 31100928 / 182681600 / 151580672 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `historical_expected_shortfall--n-1000` | `jax_jit` | 112024 / 536870912 | N/A | 31334400 / 233381888 / 202047488 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `historical_expected_shortfall--n-10000` | `numpy` | 945544 / 536870912 | 481190 | 31289344 / 182812672 / 151523328 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `historical_expected_shortfall--n-10000` | `jax_jit` | 1120024 / 536870912 | N/A | 31330304 / 233734144 / 202403840 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `historical_expected_shortfall--n-100000` | `numpy` | 8865544 / 536870912 | 4801190 | 32075776 / 185171968 / 153096192 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `historical_expected_shortfall--n-100000` | `jax_jit` | 11200024 / 536870912 | N/A | 32141312 / 236630016 / 204488704 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_variance--n-32` | `numpy` | 66824 / 536870912 | 1472 | 31240192 / 185171968 / 153931776 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_variance--n-32` | `jax_jit` | 2584 / 536870912 | N/A | 31367168 / 214450176 / 183083008 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_variance--n-252` | `numpy` | 75624 / 536870912 | 4992 | 31174656 / 185171968 / 153997312 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_variance--n-252` | `jax_jit` | 20184 / 536870912 | N/A | 30953472 / 223731712 / 192778240 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_variance--n-1000` | `numpy` | 105544 / 536870912 | 16960 | 31105024 / 185171968 / 154066944 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_variance--n-1000` | `jax_jit` | 80024 / 536870912 | N/A | 31391744 / 223752192 / 192360448 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_variance--n-10000` | `numpy` | 465544 / 536870912 | 160960 | 31174656 / 185171968 / 153997312 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_variance--n-10000` | `jax_jit` | 800024 / 536870912 | N/A | 31436800 / 199278592 / 167841792 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_variance--n-100000` | `numpy` | 4065544 / 536870912 | 1600960 | 32059392 / 185171968 / 153112576 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_variance--n-100000` | `jax_jit` | 8000024 / 536870912 | N/A | 32227328 / 199888896 / 167661568 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_volatility_intraday--n-32` | `numpy` | 66824 / 536870912 | 1472 | 31096832 / 185171968 / 154075136 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_volatility_intraday--n-32` | `jax_jit` | 2584 / 536870912 | N/A | 31285248 / 214618112 / 183332864 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_volatility_intraday--n-252` | `numpy` | 75624 / 536870912 | 4992 | 31109120 / 185171968 / 154062848 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_volatility_intraday--n-252` | `jax_jit` | 20184 / 536870912 | N/A | 31268864 / 224448512 / 193179648 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_volatility_intraday--n-1000` | `numpy` | 105544 / 536870912 | 16960 | 31182848 / 185171968 / 153989120 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_volatility_intraday--n-1000` | `jax_jit` | 80024 / 536870912 | N/A | 31404032 / 224231424 / 192827392 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_volatility_intraday--n-10000` | `numpy` | 465544 / 536870912 | 160960 | 31301632 / 185171968 / 153870336 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_volatility_intraday--n-10000` | `jax_jit` | 800024 / 536870912 | N/A | 31444992 / 215777280 / 184332288 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_volatility_intraday--n-100000` | `numpy` | 4065544 / 536870912 | 1600960 | 31944704 / 185171968 / 153227264 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_volatility_intraday--n-100000` | `jax_jit` | 8000024 / 536870912 | N/A | 32223232 / 216633344 / 184410112 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `lo_adjusted_sharpe_ratio--n-32` | `numpy` | 68872 / 536870912 | 1688 | 31092736 / 185171968 / 154079232 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `lo_adjusted_sharpe_ratio--n-32` | `jax_jit` | 3608 / 536870912 | N/A | 31322112 / 233799680 / 202477568 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `lo_adjusted_sharpe_ratio--n-252` | `numpy` | 91752 / 536870912 | 8728 | 31195136 / 185171968 / 153976832 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `lo_adjusted_sharpe_ratio--n-252` | `jax_jit` | 28248 / 536870912 | N/A | 31383552 / 235343872 / 203960320 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `lo_adjusted_sharpe_ratio--n-1000` | `numpy` | 169544 / 536870912 | 32664 | 31223808 / 185171968 / 153948160 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `lo_adjusted_sharpe_ratio--n-1000` | `jax_jit` | 112024 / 536870912 | N/A | 31264768 / 233988096 / 202723328 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `lo_adjusted_sharpe_ratio--n-10000` | `numpy` | 1105544 / 536870912 | 320664 | 31305728 / 185171968 / 153866240 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `lo_adjusted_sharpe_ratio--n-10000` | `jax_jit` | 1120024 / 536870912 | N/A | 31424512 / 234692608 / 203268096 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `lo_adjusted_sharpe_ratio--n-100000` | `numpy` | 10465544 / 536870912 | 3200664 | 31940608 / 185171968 / 153231360 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `lo_adjusted_sharpe_ratio--n-100000` | `jax_jit` | 11200024 / 536870912 | N/A | 32186368 / 242712576 / 210526208 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `kupiec_unconditional_coverage_test--n-32` | `numpy` | 74296 / 536870912 | 2344 | 31199232 / 185171968 / 153972736 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `kupiec_unconditional_coverage_test--n-32` | `jax_jit` | 9352 / 536870912 | N/A | 31219712 / 229937152 / 198717440 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `kupiec_unconditional_coverage_test--n-252` | `numpy` | 134136 / 536870912 | 7624 | 31170560 / 185171968 / 154001408 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `kupiec_unconditional_coverage_test--n-252` | `jax_jit` | 72712 / 536870912 | N/A | 31223808 / 234168320 / 202944512 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `kupiec_unconditional_coverage_test--n-1000` | `numpy` | 337592 / 536870912 | 26248 | 31215616 / 185171968 / 153956352 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `kupiec_unconditional_coverage_test--n-1000` | `jax_jit` | 288136 / 536870912 | N/A | 31272960 / 234156032 / 202883072 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `kupiec_unconditional_coverage_test--n-10000` | `numpy` | 2785592 / 536870912 | 251248 | 31371264 / 185171968 / 153800704 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `kupiec_unconditional_coverage_test--n-10000` | `jax_jit` | 2880136 / 536870912 | N/A | 31408128 / 231616512 / 200208384 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `kupiec_unconditional_coverage_test--n-100000` | `numpy` | 27265592 / 536870912 | 2501248 | 32915456 / 187998208 / 155082752 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `kupiec_unconditional_coverage_test--n-100000` | `jax_jit` | 28800136 / 536870912 | N/A | 33132544 / 234754048 / 201621504 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_independence_test--n-32` | `numpy` | 74336 / 536870912 | 3575 | 31174656 / 187998208 / 156823552 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_independence_test--n-32` | `jax_jit` | 9456 / 536870912 | N/A | 31334400 / 245821440 / 214487040 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_independence_test--n-252` | `numpy` | 134176 / 536870912 | 10835 | 31272960 / 187998208 / 156725248 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_independence_test--n-252` | `jax_jit` | 72816 / 536870912 | N/A | 31236096 / 249446400 / 218210304 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_independence_test--n-1000` | `numpy` | 337632 / 536870912 | 35583 | 31174656 / 187998208 / 156823552 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_independence_test--n-1000` | `jax_jit` | 288240 / 536870912 | N/A | 31260672 / 251179008 / 219918336 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_independence_test--n-10000` | `numpy` | 2785632 / 536870912 | 318159 | 31330304 / 187998208 / 156667904 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_independence_test--n-10000` | `jax_jit` | 2880240 / 536870912 | N/A | 31326208 / 246964224 / 215638016 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_independence_test--n-100000` | `numpy` | 27265632 / 536870912 | 2701845 | 32964608 / 188231680 / 155267072 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_independence_test--n-100000` | `jax_jit` | 28800240 / 536870912 | N/A | 33099776 / 251355136 / 218255360 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_conditional_coverage_test--n-32` | `numpy` | 74368 / 536870912 | 3575 | 31170560 / 188231680 / 157061120 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_conditional_coverage_test--n-32` | `jax_jit` | 9536 / 536870912 | N/A | 31215616 / 247558144 / 216342528 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_conditional_coverage_test--n-252` | `numpy` | 134208 / 536870912 | 10835 | 31207424 / 188231680 / 157024256 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_conditional_coverage_test--n-252` | `jax_jit` | 72896 / 536870912 | N/A | 31363072 / 253517824 / 222154752 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_conditional_coverage_test--n-1000` | `numpy` | 337664 / 536870912 | 35583 | 31105024 / 188231680 / 157126656 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_conditional_coverage_test--n-1000` | `jax_jit` | 288320 / 536870912 | N/A | 31285248 / 253878272 / 222593024 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_conditional_coverage_test--n-10000` | `numpy` | 2785664 / 536870912 | 318159 | 31363072 / 188231680 / 156868608 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_conditional_coverage_test--n-10000` | `jax_jit` | 2880320 / 536870912 | N/A | 31514624 / 248610816 / 217096192 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_conditional_coverage_test--n-100000` | `numpy` | 27265664 / 536870912 | 2701845 | 32964608 / 188559360 / 155594752 | 1 / 1 / 1 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_conditional_coverage_test--n-100000` | `jax_jit` | 28800320 / 536870912 | N/A | 33099776 / 253947904 / 220848128 | 1 / 1 / 1 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `historical_expected_shortfall--paths-100` | `numpy` | 2283936 / 536870912 | 26680 | 31100928 / 188559360 / 157458432 | 100 / 1 / 100 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `historical_expected_shortfall--paths-100` | `jax_jit` | 2824800 / 536870912 | N/A | 31162368 / 233332736 / 202170368 | 100 / 1 / 100 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `historical_expected_shortfall--paths-1000` | `numpy` | 22249536 / 536870912 | 253480 | 30998528 / 188559360 / 157560832 | 1000 / 1 / 1000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `historical_expected_shortfall--paths-1000` | `jax_jit` | 28248000 / 536870912 | N/A | 31207424 / 239513600 / 208306176 | 1000 / 1 / 1000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `historical_expected_shortfall--paths-10000` | `numpy` | 221905536 / 536870912 | 2521480 | 31072256 / 206131200 / 175058944 | 10000 / 1 / 10000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `historical_expected_shortfall--paths-10000` | `jax_jit` | 282480000 / 536870912 | N/A | 31240192 / 296910848 / 265670656 | 10000 / 1 / 10000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_variance--paths-100` | `numpy` | 1074336 / 536870912 | 26680 | 31059968 / 206131200 / 175071232 | 100 / 1 / 100 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_variance--paths-100` | `jax_jit` | 2018400 / 536870912 | N/A | 30990336 / 206131200 / 175140864 | 100 / 1 / 100 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_variance--paths-1000` | `numpy` | 10153536 / 536870912 | 253480 | 31010816 / 206131200 / 175120384 | 1000 / 1 / 1000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_variance--paths-1000` | `jax_jit` | 20184000 / 536870912 | N/A | 30961664 / 206131200 / 175169536 | 1000 / 1 / 1000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_variance--paths-10000` | `numpy` | 100945536 / 536870912 | 2521480 | 31014912 / 206340096 / 175325184 | 10000 / 1 / 10000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_variance--paths-10000` | `jax_jit` | 201840000 / 536870912 | N/A | 31170560 / 223584256 / 192413696 | 10000 / 1 / 10000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_volatility_intraday--paths-100` | `numpy` | 1074336 / 536870912 | 26680 | 31064064 / 206340096 / 175276032 | 100 / 1 / 100 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_volatility_intraday--paths-100` | `jax_jit` | 2018400 / 536870912 | N/A | 31174656 / 218214400 / 187039744 | 100 / 1 / 100 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_volatility_intraday--paths-1000` | `numpy` | 10153536 / 536870912 | 253480 | 31072256 / 206340096 / 175267840 | 1000 / 1 / 1000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_volatility_intraday--paths-1000` | `jax_jit` | 20184000 / 536870912 | N/A | 31137792 / 220270592 / 189132800 | 1000 / 1 / 1000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `realized_volatility_intraday--paths-10000` | `numpy` | 100945536 / 536870912 | 2521480 | 31068160 / 206442496 / 175374336 | 10000 / 1 / 10000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `realized_volatility_intraday--paths-10000` | `jax_jit` | 201840000 / 536870912 | N/A | 31223808 / 223956992 / 192733184 | 10000 / 1 / 10000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `lo_adjusted_sharpe_ratio--paths-100` | `numpy` | 2687136 / 536870912 | 26680 | 31096832 / 206442496 / 175345664 | 100 / 1 / 100 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `lo_adjusted_sharpe_ratio--paths-100` | `jax_jit` | 2824800 / 536870912 | N/A | 31182848 / 255610880 / 224428032 | 100 / 1 / 100 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `lo_adjusted_sharpe_ratio--paths-1000` | `numpy` | 26281536 / 536870912 | 253480 | 30973952 / 206442496 / 175468544 | 1000 / 1 / 1000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `lo_adjusted_sharpe_ratio--paths-1000` | `jax_jit` | 28248000 / 536870912 | N/A | 31158272 / 276738048 / 245579776 | 1000 / 1 / 1000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `lo_adjusted_sharpe_ratio--paths-10000` | `numpy` | 262225536 / 536870912 | 2521480 | 31010816 / 206721024 / 175710208 | 10000 / 1 / 10000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `lo_adjusted_sharpe_ratio--paths-10000` | `jax_jit` | 282480000 / 536870912 | N/A | 31236096 / 377503744 / 346267648 | 10000 / 1 / 10000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `kupiec_unconditional_coverage_test--paths-100` | `numpy` | 6925536 / 536870912 | 51944 | 31014912 / 206721024 / 175706112 | 100 / 1 / 100 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `kupiec_unconditional_coverage_test--paths-100` | `jax_jit` | 7271200 / 536870912 | N/A | 31186944 / 231206912 / 200019968 | 100 / 1 / 100 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `kupiec_unconditional_coverage_test--paths-1000` | `numpy` | 68665536 / 536870912 | 505544 | 31002624 / 206721024 / 175718400 | 1000 / 1 / 1000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `kupiec_unconditional_coverage_test--paths-1000` | `jax_jit` | 72712000 / 536870912 | N/A | 31133696 / 237457408 / 206323712 | 1000 / 1 / 1000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `kupiec_unconditional_coverage_test--paths-10000` | `numpy` | 506548800 / 536870912 | 51097653 | 31072256 / 228003840 / 196931584 | 7381 / 2 / 2619 / 4762 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `kupiec_unconditional_coverage_test--paths-10000` | `jax_jit` | 536833936 / 536870912 | N/A | 31485952 / 392880128 / 361394176 | 7381 / 2 / 2619 / 4762 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_independence_test--paths-100` | `numpy` | 6929536 / 536870912 | 51944 | 30932992 / 228003840 / 197070848 | 100 / 1 / 100 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_independence_test--paths-100` | `jax_jit` | 7281600 / 536870912 | N/A | 31195136 / 248946688 / 217751552 | 100 / 1 / 100 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_independence_test--paths-1000` | `numpy` | 68705536 / 536870912 | 505544 | 30998528 / 228003840 / 197005312 | 1000 / 1 / 1000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_independence_test--paths-1000` | `jax_jit` | 72816000 / 536870912 | N/A | 31264768 / 260689920 / 229425152 | 1000 / 1 / 1000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_independence_test--paths-10000` | `numpy` | 506126272 / 536870912 | 52013512 | 31055872 / 232919040 / 201863168 | 7369 / 2 / 2631 / 4738 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_independence_test--paths-10000` | `jax_jit` | 536833680 / 536870912 | N/A | 31170560 / 442650624 / 411480064 | 7369 / 2 / 2631 / 4738 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_conditional_coverage_test--paths-100` | `numpy` | 6932736 / 536870912 | 51944 | 31010816 / 232919040 / 201908224 | 100 / 1 / 100 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_conditional_coverage_test--paths-100` | `jax_jit` | 7289600 / 536870912 | N/A | 31227904 / 250306560 / 219078656 | 100 / 1 / 100 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_conditional_coverage_test--paths-1000` | `numpy` | 68737536 / 536870912 | 505544 | 30998528 / 232919040 / 201920512 | 1000 / 1 / 1000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_conditional_coverage_test--paths-1000` | `jax_jit` | 72896000 / 536870912 | N/A | 31125504 / 263442432 / 232316928 | 1000 / 1 / 1000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `christoffersen_conditional_coverage_test--paths-10000` | `numpy` | 505829376 / 536870912 | 53078400 | 31023104 / 235569152 / 204546048 | 7360 / 2 / 2640 / 4720 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `christoffersen_conditional_coverage_test--paths-10000` | `jax_jit` | 536852480 / 536870912 | N/A | 31256576 / 442494976 / 411238400 | 7360 / 2 / 2640 / 4720 | `jax_compiled_memory_analysis_plus_host_v1` |
| `probabilistic_sharpe_ratio--paths-100` | `numpy` | 86336 / 536870912 | 4673 | 31035392 / 235569152 / 204533760 | 100 / 1 / 100 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `probabilistic_sharpe_ratio--paths-100` | `jax_jit` | 42400 / 536870912 | N/A | 31002624 / 235569152 / 204566528 | 100 / 1 / 100 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `probabilistic_sharpe_ratio--paths-1000` | `numpy` | 273536 / 536870912 | 40453 | 31031296 / 235569152 / 204537856 | 1000 / 1 / 1000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `probabilistic_sharpe_ratio--paths-1000` | `jax_jit` | 424000 / 536870912 | N/A | 30859264 / 235569152 / 204709888 | 1000 / 1 / 1000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `probabilistic_sharpe_ratio--paths-10000` | `numpy` | 2145536 / 536870912 | 404773 | 31027200 / 235569152 / 204541952 | 10000 / 1 / 10000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `probabilistic_sharpe_ratio--paths-10000` | `jax_jit` | 4240000 / 536870912 | N/A | 30978048 / 235569152 / 204591104 | 10000 / 1 / 10000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `deflated_sharpe_ratio--paths-100` | `numpy` | 90336 / 536870912 | 10128 | 31068160 / 235569152 / 204500992 | 100 / 1 / 100 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `deflated_sharpe_ratio--paths-100` | `jax_jit` | 50400 / 536870912 | N/A | 31059968 / 235814912 / 204754944 | 100 / 1 / 100 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `deflated_sharpe_ratio--paths-1000` | `numpy` | 313536 / 536870912 | 44989 | 30998528 / 235569152 / 204570624 | 1000 / 1 / 1000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `deflated_sharpe_ratio--paths-1000` | `jax_jit` | 504000 / 536870912 | N/A | 31186944 / 237543424 / 206356480 | 1000 / 1 / 1000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |
| `deflated_sharpe_ratio--paths-10000` | `numpy` | 2545536 / 536870912 | 409309 | 30994432 / 235569152 / 204574720 | 10000 / 1 / 10000 / 0 | `numpy_source_bound_plus_tracemalloc_preflight_v2` |
| `deflated_sharpe_ratio--paths-10000` | `jax_jit` | 5040000 / 536870912 | N/A | 31125504 / 236630016 / 205504512 | 10000 / 1 / 10000 / 0 | `jax_compiled_memory_analysis_plus_host_v1` |

## Comparison eligibility / 비교 자격

| Case | Phase | All eligible | Same timed boundary | Speedup | Eligibility record |
|---|---|---|---|---:|---|
| `historical_expected_shortfall--n-32` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--n-32` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--n-252` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--n-252` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--n-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--n-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--n-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--n-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--n-100000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--n-100000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-32` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-32` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-252` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-252` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-100000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--n-100000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-32` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-32` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-252` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-252` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-100000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--n-100000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-32` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-32` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-252` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-252` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-100000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--n-100000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-32` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-32` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-252` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-252` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-100000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--n-100000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-32` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-32` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-252` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-252` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-100000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--n-100000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-32` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-32` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-252` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-252` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-100000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--n-100000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--paths-100` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--paths-100` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--paths-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--paths-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--paths-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `historical_expected_shortfall--paths-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--paths-100` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--paths-100` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--paths-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--paths-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--paths-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_variance--paths-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--paths-100` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--paths-100` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--paths-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--paths-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--paths-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `realized_volatility_intraday--paths-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--paths-100` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--paths-100` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--paths-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--paths-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--paths-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `lo_adjusted_sharpe_ratio--paths-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--paths-100` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--paths-100` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--paths-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--paths-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--paths-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `kupiec_unconditional_coverage_test--paths-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--paths-100` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--paths-100` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--paths-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--paths-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--paths-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_independence_test--paths-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--paths-100` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--paths-100` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--paths-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--paths-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--paths-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `christoffersen_conditional_coverage_test--paths-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `probabilistic_sharpe_ratio--paths-100` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `probabilistic_sharpe_ratio--paths-100` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `probabilistic_sharpe_ratio--paths-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `probabilistic_sharpe_ratio--paths-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `probabilistic_sharpe_ratio--paths-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `probabilistic_sharpe_ratio--paths-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `deflated_sharpe_ratio--paths-100` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `deflated_sharpe_ratio--paths-100` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `deflated_sharpe_ratio--paths-1000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `deflated_sharpe_ratio--paths-1000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `deflated_sharpe_ratio--paths-10000` | `cold_total` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |
| `deflated_sharpe_ratio--paths-10000` | `warm` | false | false | N/A | `{"sameAffinity": true, "sameExecutionBoundary": true, "sameFixture": true, "sameHost": true, "sameRun": true, "sameThreads": true, "sameTimedBoundary": false}` |

## Environment / 환경

- Execution boundary: `wsl2`
- Outer host boundary: `wsl2`
- Host fingerprint: `688f581b0da53c0c494b296084d858b6e50e7945927af1a827295869b813a748`
- OS/kernel/architecture: `Linux` / `6.18.33.2-microsoft-standard-WSL2` / `x86_64`
- WSL version evidence: `{"status": "measured", "value": "WSL 버전: 2.7.10.0 | 커널 버전: 6.18.33.2-2 | WSLg 버전: 1.0.73.2 | MSRDC 버전: 1.2.6676"}`
- CPU model: `Intel(R) Core(TM) Ultra 5 228V`
- Physical/logical cores: 8 / 8
- CPU affinity: `[0]`
- CPU governor: `{"reason": "CPU governor is not exposed by this execution boundary", "status": "not_applicable"}`
- Memory bytes: 16508379136
- Python/NumPy/JAX/JAXLIB: `3.12.13` / `2.5.1` / `0.11.0` / `0.11.0`
- Backend/devices/x64: `cpu` / `[{"deviceKind": "cpu", "id": 0, "platform": "cpu"}]` / True
- Thread environment: `{"MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}`
- Container runtime/image: `{"reason": "host benchmark", "status": "not_applicable"}` / `{"reason": "host benchmark", "status": "not_applicable"}`

## DSR provenance / DSR 출처

- Record: `{"effectiveTrialCount": 2, "method": "pre_registered_independent", "rawTrialCount": 2, "registrySerialization": "strict-json-sort-keys-utf8-v1", "samplingFrequency": "daily", "schemaVersion": "s1.4r-effective-trials-v1", "sharpeEstimateVariance": 0.04, "trialRegistrySha256": "a40fd68290a4dfadabc80e16e9adba4226e8a470e336774afff548829825e706", "varianceDdof": 1}`
- The benchmark uses the strict-JSON trial registry digest, two pre-registered independent daily trials, and ddof=1 variance. The host validates provenance and independently checks the expected probability before timing. The JAX DSR numeric core recomputes SR* from N and variance, including log-tail inverse-normal arithmetic, inside the compiled timing boundary.

## Artifact sizes and identity / 산출물 크기와 동일성

- Research wheel: 19689 bytes; SHA-256 `95437a8ff18ccb6151c94b85224bc4215552fcbcb47ce70041141728eee06c67`
- Installed research environment: 768809969 bytes (apparent_bytes)
- OCI image: 255102992 bytes; ID `sha256:a2472f838329657209f4be4f4e44bc4ee2aac44532e82ad051ad2019d1789472`; manifest `sha256:a2472f838329657209f4be4f4e44bc4ee2aac44532e82ad051ad2019d1789472`; docker_image_inspect_size_single_build_docker_descriptor_matches_oci_manifest
- OCI archive: 255118848 uncompressed bytes; 252983142 compressed bytes; SHA-256 `60efdf2f58c4b91c48ac338d738f3d49c5a47935bf35c5c6b504d876bffd70de`
- Native executable: `{"reason": "separate native executable is outside S1.4R scope", "status": "not_applicable"}`

## Limitations and conclusion / 한계와 결론

- KR: 수치는 이 host/run/affinity/thread 환경에 한정되며 CPU 주파수, 스케줄링, compiler 상태의 영향을 받는다. PSR/DSR은 작은 표본에서 asymptotic 한계가 있다. RSS는 runtime baseline을 포함한 진단값이다.
- EN: Measurements are specific to this host/run/affinity/thread context and remain sensitive to CPU frequency, scheduling, and compiler state. PSR/DSR retain small-sample asymptotic limitations. RSS is diagnostic and includes runtime baseline effects.
- KR/EN: NumPy와 JAX의 timed boundary가 다르므로 이 manifest는 speedup ratio를 보고하지 않는다 / This manifest reports no speedup ratio because the NumPy and JAX timed boundaries differ.
- KR/EN: 이는 격리된 research evidence이며 production 구현 교체 결론을 내리지 않는다 / This is isolated research evidence and makes no production replacement conclusion.
