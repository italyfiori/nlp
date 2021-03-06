# -*- coding: utf-8 -*-
"""
File:    my_crf
Author:  yulu04
Date:    2019/1/8
Desc:
"""
import numpy as np
import math
import re

MAX_SCALE_THRESHOLD = 1e250


class Crf(object):
    LABEL_START = '|-'
    LABEL_END = '-|'
    LABEL_INDEX_START = 0
    LABEL_INDEX_END = None
    LABEL_INDEX_NONE = None
    OBSERVE_END = ('\n', '\n')
    feature_templates = None

    def __init__(self):
        self.features_dict = None  # 特征词典 { x_feature : { (y_prev, y) : feature_id } }
        self.labels_dict = None  # 特征标签 { label: label_id}
        self.labels_index = None  # 特征标签 {label_id : label}
        self.features_counts = None  # 特征数量 { feature_id : counts }
        self.weights = None  # 特征权重
        self.squared_sigma = 10.0

    def train(self, data, rate=0.0002, iterations=100, squared_sigma=10.0):
        """
        输入数据格式:
        训练数据: 前2列是输入(单词和词性) 最后一列是标志
        Confidence NN B-NP
        in IN B-PP
        the DT B-NP
        pound NN I-NP
        is VBZ B-VP
        widely RB I-VP
        expected VBN I-VP
        to TO I-VP
        take VB I-VP
        another DT B-NP
        sharp JJ I-NP
        dive NN I-NP
        if IN B-SBAR
        trade NN B-NP
        figures NNS I-NP
        for IN B-PP
        September NNP B-NP
        , , O
        
        特征模板: 前2列的特征, 第1个数字对应时间偏移, 第2个数字代表列数
        U01:%x[0,0]
        U02:%x[0,1]
        U03:%x[1,0]
        U04:%x[0,0]/%x[1,0]
        U04:%x[1,1]
        U04:%x[0,1]/%x[1,1]
        U04:%x[2,0]
        U04:%x[2,1]
        U04:%x[1,1]/%x[2,1]
        U04:%x[0,1]/%x[1,1]/%x[2,1]
        U04:%x[-1,0]
        U04:%x[-1,0]/%x[0,0]
        U04:%x[-1,1]
        U04:%x[-1,1]/%x[0,1]
        U04:%x[-1,1]/%x[0,1]/%x[1,1]
        U04:%x[-2,0]
        U04:%x[-2,1]
        U04:%x[-2,1]/%x[-1,1]
        U04:%x[-2,1]/%x[-1,1]/%x[0,1]
        
        X特征:
        ['1_Confidence', '2_NN', '3_in', '4_Confidence_in', '5_IN', '6_NN_IN', '7_the', '8_DT', '9_IN_DT', '10_NN_IN']
        
        Y特征:
        [(y_prev_idx, y_idx), (self.LABEL_INDEX_NONE, y_idx)]
        
        特征函数:
        X与Y的组合
        """
        self.features_dict = {}
        self.labels_dict = {}
        self.labels_index = {}
        self.features_counts = {}

        print('star generate features')
        self.generate_features(data)
        self.weights = np.zeros(self.features_counts.shape[0])
        print('find %d features' % len(self.weights))

        for i in range(iterations):
            likelihood, gradients = self.calc_likelihood_and_gradient(data, self.weights,
                                                                      self.features_counts,
                                                                      squared_sigma)

            l2 = np.sum(np.square(gradients))
            print(i, 'likelihood:', likelihood, 'l2:', l2)
            self.weights += rate * gradients

    def predict(self, X):
        """
        预测观测序列对应的标记序列
        :param X:
        :return:
        """
        # 计算观测序列的非规范化转移概率矩阵序列
        trans_matrix_list = self.generate_trans_matrix_list(self.weights, X)

        # 保存状态路径的最大值概率值, 和最优状态路径
        viterbi_matrix = []
        viterbi_path = {}

        # 初始状态概率
        viterbi_matrix.append({})
        for label in self.labels_index:
            viterbi_matrix[0][label] = trans_matrix_list[0][self.LABEL_INDEX_START, label]
            viterbi_path[label] = [label]

        for t in range(1, len(X)):
            # 防止路径联合概率过大
            if max(viterbi_matrix[-1].values()) > MAX_SCALE_THRESHOLD:
                viterbi_matrix[-1] = {label: prob / MAX_SCALE_THRESHOLD for label, prob in
                                      viterbi_matrix[-1].items()}

            trans_matrix = trans_matrix_list[t]
            viterbi_matrix.append({})
            viterbi_path_tmp = {}

            for cur_label in self.labels_index:
                cur_prob, prev_label = max([(viterbi_matrix[t - 1][prev_label] * trans_matrix[
                    prev_label, cur_label], prev_label) for prev_label in self.labels_index])

                viterbi_matrix[t][cur_label] = cur_prob
                viterbi_path_tmp[cur_label] = viterbi_path[prev_label] + [cur_label]

            viterbi_path = viterbi_path_tmp

        last_prob, last_label = max(
            [(viterbi_matrix[-1][label], label) for label in self.labels_index])

        label_path = viterbi_path[last_label]
        return [self.labels_index[label] for label in label_path]

    # 计算似然函数和梯度
    def calc_likelihood_and_gradient(self, data, weights, features_empirical_counts, squared_sigma):
        """
        :param data: 训练集
        :param weights: 特征函数权重
        :param features_empirical_counts: 特征函数的经验概率
        :param squared_sigma: 惩罚因子
        :return: 似然函数值和梯度
        """
        # 初始化特征函数的数学期望
        features_expect_counts = np.zeros((len(features_empirical_counts)))

        total_Z = 0
        for X, _ in data:
            # 计算转移概率矩阵
            trans_matrix_list = self.generate_trans_matrix_list(weights, X)
            # 计算前向后向概率
            alpha_matrix, beta_matrix, Z, scale_matrix = self.forward_backward(X, trans_matrix_list)

            total_Z += math.log(Z) + np.sum(np.log(scale_matrix))

            for t in range(len(X)):
                trans_matrix = trans_matrix_list[t]

                # 观测序列X在t时刻的特征函数集合
                feature_funcs = self.get_feature_funcs_from_dict(X, t)
                for (y_prev, y), feature_ids in feature_funcs.items():

                    if t == 0 and y_prev is not self.LABEL_INDEX_START and y_prev is not self.LABEL_INDEX_NONE:
                        continue
                    if t > 0 and y_prev is self.LABEL_INDEX_START:
                        continue

                    if t == 0 and y_prev is self.LABEL_INDEX_START:
                        feature_prob = trans_matrix[self.LABEL_INDEX_START, y] * beta_matrix[
                            t, y] / Z
                    elif y_prev is self.LABEL_INDEX_NONE:
                        feature_prob = alpha_matrix[t, y] * beta_matrix[t, y] * scale_matrix[t] / Z
                    else:
                        feature_prob = alpha_matrix[t - 1, y_prev] * trans_matrix[y_prev, y] * \
                                       beta_matrix[t, y] / Z

                    # 计算特征函数的数学期望
                    for feature_id in feature_ids:
                        features_expect_counts[feature_id] += feature_prob

        # 计算似然函数值(标量)
        likelihood = np.dot(features_empirical_counts.T, weights) - total_Z - np.sum(
            np.dot(weights, weights)) / (squared_sigma * 2)

        # 计算梯度(向量)
        gradient = features_empirical_counts - features_expect_counts - weights / squared_sigma

        return -likelihood, gradient

    def generate_trans_matrix_list(self, weights, X):
        """
        计算观测序列X在所有时刻的非规范化转移概率矩阵组成的列表
        :param weights: 特征函数权重
        :param X: 观测序列X
        :return: X在所有时刻的转移概率矩阵列表 M(start, y0), M(y0, y1), ... , M(yn-1, end)
        """
        _X = X
        trans_matrix_list = np.zeros((len(_X), len(self.labels_dict), len(self.labels_dict)))

        for t in range(len(_X)):
            trans_matrix_list[t] = self.generate_trans_matrix(weights, _X, t)

        return trans_matrix_list

    def generate_trans_matrix(self, weights, X, t):
        """
        计算观测序列在t时刻的转移概率矩阵
        :param weights: 特征函数权重
        :param X: 观测序列X
        :param t: 时刻t
        :return: 转移概率矩阵M(y_prev, y|X), 大小为(labels_num, labels_num),
        """
        labels_num = len(self.labels_dict)
        trans_matrix = np.zeros((labels_num, labels_num))

        feature_funcs = self.get_feature_funcs_from_dict(X, t)
        for (y_prev, y), feature_ids in feature_funcs.items():
            weights_sum = sum([weights[feature_id] for feature_id in feature_ids])

            if y_prev == self.LABEL_INDEX_NONE:
                trans_matrix[:, y] += weights_sum
            else:
                trans_matrix[y_prev, y] += weights_sum

        trans_matrix = np.exp(trans_matrix)
        if t == 0:
            # 0时刻,y_prev都为START状态
            trans_matrix[self.LABEL_INDEX_START + 1:] = 0
        else:
            # 其他时刻, y_prev和y都不能为START状态
            trans_matrix[:, self.LABEL_INDEX_START] = 0
            trans_matrix[self.LABEL_INDEX_START, :] = 0

        return trans_matrix

    def forward_backward(self, X, trans_matrix_list):
        """
        计算观测序列X的前向概率、后向概率和归一化因子
        :param X: 观测序列X, 大小为 ( T )
        :param trans_matrix_list: X在所有时刻的转移概率矩阵列表[ Mt(yt_prev, yt|X) ]，大小为(T, labels_num, labels_num)
        :return: 观测序列X的前向概率矩阵alpha(T+1, labels_num), 后向概率矩阵beta(T+1, labels_num), 归一化因子Z(标量)
        """
        matrix_len = len(X)
        assert matrix_len == len(trans_matrix_list)

        alpha_matrix = np.zeros((matrix_len, len(self.labels_dict)))

        scale_matrix = np.ones((matrix_len,))

        # 计算前向概率, 省略了start时刻
        alpha_matrix[0, :] = trans_matrix_list[0][self.LABEL_INDEX_START, :]

        for t in range(1, matrix_len):
            alpha_matrix[t] = np.dot(alpha_matrix[t - 1, :], trans_matrix_list[t])

            if any(alpha_matrix[t] > MAX_SCALE_THRESHOLD):
                alpha_matrix[t] /= MAX_SCALE_THRESHOLD
                scale_matrix[t] = MAX_SCALE_THRESHOLD

        # 计算后向概率, 省略了end时刻
        # beta_matrix[-1, :] = trans_matrix_list[matrix_len][:, self.LABEL_INDEX_END]
        beta_matrix = np.zeros((matrix_len, len(self.labels_dict)))
        beta_matrix[-1, :] = 1.0
        for t in range(matrix_len - 2, -1, -1):
            beta_matrix[t] = np.dot(trans_matrix_list[t + 1], beta_matrix[t + 1, :])
            beta_matrix[t] /= scale_matrix[t]

        # 归一化因子
        Z = sum(alpha_matrix[-1])
        return alpha_matrix, beta_matrix, Z, scale_matrix

    # 保存模型
    def save_model(self):
        pass

    # 计算模型
    def load_model(self):
        pass

    def data2features(self, data):
        """
        将观测序列数据转换成特征函数的表示形式。 m个样本, 每个样本长度为T, 每个时刻有K个特征函数
        特征函数的形式为:  { (y_prev, y) : feature_ids }
        :param data:
        :return:
        """
        data_features = []
        for X, _ in data:
            data_features.append([self.get_feature_funcs_from_dict(X, t) for t in range(len(X))])

        return data_features

    def generate_features(self, data):
        """
        生成表示特征函数的相关变量
        self.labels_dict: { y : y_id}
        self.features_dict: { x_feature : { (y_prev_id, y_id) : feature_id } }
        self.features_counts: { feature_id : counts }
        :param data:
        :return:
        """
        self.labels_dict[self.LABEL_START] = self.LABEL_INDEX_START

        for _, Y in data:
            for t in range(len(Y)):
                y = Y[t]
                if y not in self.labels_dict:
                    self.labels_dict[y] = len(self.labels_dict)

        # self.labels_dict[self.LABEL_END] = self.LABEL_INDEX_END = len(self.labels_dict)
        self.labels_index = {label_id: label for label, label_id in self.labels_dict.items()}

        for X, Y in data:
            for t in range(len(X)):
                x_features = self.get_x_features_from_template(X, t)

                y = Y[t]
                y_prev = Y[t - 1] if t > 0 else self.LABEL_START
                y_idx = self.labels_dict[y]
                y_prev_idx = self.labels_dict[y_prev]
                y_features = [(y_prev_idx, y_idx), (self.LABEL_INDEX_NONE, y_idx)]

                self.fill_features(x_features, y_features)

        # features_counts 转换成1d array
        self.features_counts = np.array(list(self.features_counts.values()))

    def fill_features(self, x_features, y_features):
        """
        填充表示特征函数的相关变量
        self.features_dict: { x_feature : { (y_prev_id, y_id) : feature_id } }
        self.features_counts: { feature_id : counts }
        :param x_features:
        :param y_features:
        :return:
        """
        for x_feature in x_features:
            if x_feature not in self.features_dict:
                self.features_dict[x_feature] = {}
            for y_feature in y_features:
                if y_feature not in self.features_dict[x_feature]:
                    # 首次出现的特征函数, 保存到词典
                    self.features_dict[x_feature][y_feature] = len(self.features_counts)

                feature_id = self.features_dict[x_feature][y_feature]
                self.features_counts[feature_id] = self.features_counts.get(feature_id, 0) + 1

    def get_feature_funcs_from_dict(self, X, t):
        """
        根据特征模板和词典提取获观测序列X在t时刻对应的特征函数集合: { (y_prev_id, y_id) : feature_ids }
        :param X:
        :param t:
        :return:
        """
        feature_funcs = {}

        x_features = self.get_x_features_from_template(X, t)
        for x_feature in x_features:
            if x_feature not in self.features_dict:
                continue

            for (y_prev, y), feature_id in self.features_dict[x_feature].items():
                if (y_prev, y) not in feature_funcs:
                    feature_funcs[(y_prev, y)] = set()
                feature_funcs[(y_prev, y)].add(feature_id)
        return feature_funcs

    def read_feature_template(self, file_path):
        """
        读取CRF++形式的特征模板文件
        :param file_path:
        :return:
        """
        feature_templates = []
        f = open(file_path)
        lines = f.readlines()
        pattern = re.compile(r'%x\[(-?\d),(-?\d)]')

        for line in lines:
            feature_positions = pattern.findall(line)
            feature_positions = [(int(x), int(y)) for x, y in feature_positions]
            if len(feature_positions) > 0:
                feature_templates.append(feature_positions)

        self.feature_templates = feature_templates

    def get_x_features_from_template(self, X, t):
        """
        从特征函数模板中获取观测序列X在t时刻的x特征集合([x_feature1, x_feature2, ...])
        :param X:
        :param t:
        :return:
        """
        x_features = list()

        i = 0
        X_len = len(X)
        for feature_positions in self.feature_templates:
            i += 1
            x_feature = ""
            for feature_position in feature_positions:
                t_offset = feature_position[0]
                v_offset = feature_position[1]
                t_position = t + t_offset

                if t_position < 0 or t_position >= X_len:
                    x_feature = ""
                    break

                # 取相对于当前观测位置偏移t_offset的观测值作为特征
                x = X[t_position]

                if type(x) == list or type(x) == tuple:
                    # 取特征的第v_offset位
                    assert v_offset < len(x)
                    x_feature += '_' + x[v_offset]
                else:
                    x_feature += '_' + x

            if x_feature != "":
                x_feature = str(i) + x_feature
                x_features.append(x_feature)
                
        return x_features
