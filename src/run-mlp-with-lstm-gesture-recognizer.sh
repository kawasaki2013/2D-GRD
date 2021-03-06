#!/bin/bash
# Copyright 2018 Ryohei Kamiya <ryohei.kamiya@lab2biz.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


python3 gesture_recognizer.py \
  -n mlp-with-lstm -mlp ../models/mlp-parameters.h5 \
  -lstm ../models/lstm-with-baseshift-parameters.h5 \
  -l ../models/labels.txt \
  -xl 64 -xil 16 -xol 1 -xss 1
