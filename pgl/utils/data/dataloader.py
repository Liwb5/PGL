#-*- coding: utf-8 -*-
# Copyright (c) 2019 PaddlePaddle Authors. All Rights Reserved
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
"""dataloader
"""

import numpy as np

import paddle
import paddle.fluid as F
import paddle.fluid.layers as L

from pgl.utils import mp_reader
from pgl.utils.data.dataset import Dataset, StreamDataset
from pgl.utils.data.sampler import Sampler, StreamSampler


class Dataloader(object):
    """Dataloader
    """

    def __init__(
            self,
            dataset,
            batch_size=1,
            drop_last=False,
            shuffle=False,
            num_workers=1,
            collate_fn=None,
            buf_size=1000, ):

        self.dataset = dataset
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.num_workers = num_workers
        self.collate_fn = collate_fn
        self.buf_size = buf_size
        self.drop_last = drop_last

    def __len__(self):
        if not isinstance(self.dataset, StreamDataset):
            return len(self.sampler)
        else:
            raise "StreamDataset has no length"

    def __iter__(self):
        # generating a iterable sequence for produce batch data without repetition
        if isinstance(self.dataset, StreamDataset):  # for stream data
            self.sampler = StreamSampler(
                self.dataset,
                batch_size=self.batch_size,
                drop_last=self.drop_last)
        else:
            self.sampler = Sampler(
                self.dataset,
                batch_size=self.batch_size,
                drop_last=self.drop_last,
                shuffle=self.shuffle)

        if self.num_workers == 1:
            r = paddle.reader.buffered(_DataLoaderIter(self, 0), self.buf_size)
        else:
            worker_pool = [
                _DataLoaderIter(self, wid) for wid in range(self.num_workers)
            ]
            workers = mp_reader.multiprocess_reader(
                worker_pool, use_pipe=True, queue_size=1000)
            r = paddle.reader.buffered(workers, self.buf_size)

        for batch in r():
            yield batch

    def __call__(self):
        return self.__iter__()


class _DataLoaderIter(object):
    def __init__(self, dataloader, fid=0):
        self.dataset = dataloader.dataset
        self.sampler = dataloader.sampler
        self.collate_fn = dataloader.collate_fn
        self.num_workers = dataloader.num_workers
        self.drop_last = dataloader.drop_last
        self.fid = fid
        self.count = 0

    def _data_generator(self):
        for indices in self.sampler:

            self.count += 1
            if self.count % self.num_workers != self.fid:
                continue

            batch_data = [self.dataset[i] for i in indices]

            if self.collate_fn is not None:
                yield self.collate_fn(batch_data)
            else:
                yield batch_data

    def _streamdata_generator(self):
        dataset = iter(self.dataset)
        for indices in self.sampler:
            batch_data = []
            for _ in indices:
                try:
                    batch_data.append(next(dataset))
                except StopIteration:
                    break

            if len(batch_data) == 0 or (self.drop_last and
                                        len(batch_data) < len(indices)):
                break
                #  raise StopIteration

            # make sure do not repeat in multiprocessing 
            self.count += 1
            if self.count % self.num_workers != self.fid:
                continue

            if self.collate_fn is not None:
                yield self.collate_fn(batch_data)
            else:
                yield batch_data

    def __iter__(self):
        if isinstance(self.dataset, StreamDataset):
            data_generator = self._streamdata_generator
        else:
            data_generator = self._data_generator

        for batch_data in data_generator():
            yield batch_data

    def __call__(self):
        return self.__iter__()