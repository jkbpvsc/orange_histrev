import unittest
import numpy as np
from Orange.data import Table
from Orange.regression import (LinearRegressionLearner,
                               RidgeRegressionLearner,
                               LassoRegressionLearner,
                               ElasticNetLearner,
                               ElasticNetCVLearner,
                               MeanLearner)
from Orange.evaluation import CrossValidation, RMSE
from sklearn import linear_model


class LinearRegressionTest(unittest.TestCase):
    def test_LinearRegression(self):
        nrows = 1000
        ncols = 3
        x = np.random.random_integers(-20, 50, (nrows, ncols))
        c = np.random.rand(ncols, 1) * 10 - 3
        e = np.random.rand(nrows, 1) - 0.5
        y = np.dot(x, c) + e

        x1, x2 = np.split(x, 2)
        y1, y2 = np.split(y, 2)
        t = Table(x1, y1)
        learn = LinearRegressionLearner()
        clf = learn(t)
        z = clf(x2)
        self.assertTrue((abs(z.reshape(-1, 1) - y2) < 2.0).all())

    def test_Regression(self):
        data = Table("housing")
        ridge = RidgeRegressionLearner()
        lasso = LassoRegressionLearner()
        elastic = ElasticNetLearner()
        elasticCV = ElasticNetCVLearner()
        mean = MeanLearner()
        learners = [ridge, lasso, elastic, elasticCV, mean]
        res = CrossValidation(data, learners, k=2)
        rmse = RMSE(res)
        for i in range(len(learners) - 1):
            self.assertTrue(rmse[i] < rmse[-1])

    def test_linear_scorer(self):
        data = Table('housing')
        learner = LinearRegressionLearner()
        scores = learner.score_data(data)
        self.assertEqual('NOX',
                         data.domain.attributes[np.argmax(scores)].name)
        self.assertEqual(len(scores), len(data.domain.attributes))

    def test_ridge_scorer(self):
        data = Table('housing')
        learner = RidgeRegressionLearner()
        scores = learner.score_data(data)
        self.assertEqual(len(scores), len(data.domain.attributes))

    def test_lasso_scorer(self):
        data = Table('housing')
        learner = LassoRegressionLearner()
        scores = learner.score_data(data)
        self.assertEqual(len(scores), len(data.domain.attributes))

    def test_linear_scorer_feature(self):
        data = Table('housing')
        learner = LinearRegressionLearner()
        scores = learner.score_data(data)
        for i, attr in enumerate(data.domain.attributes):
            score = learner.score_data(data, attr)
            self.assertEqual(score, scores[i])

    def test_ridge_scorer_feature(self):
        data = Table('housing')
        learner = RidgeRegressionLearner()
        scores = learner.score_data(data)
        for i, attr in enumerate(data.domain.attributes):
            score = learner.score_data(data, attr)
            self.assertEqual(score, scores[i])

    def test_lasso_scorer_feature(self):
        data = Table('housing')
        learner = LassoRegressionLearner()
        scores = learner.score_data(data)
        for i, attr in enumerate(data.domain.attributes):
            score = learner.score_data(data, attr)
            self.assertEqual(score, scores[i])

    def test_coefficients(self):
        data = Table([[11], [12], [13]], [0, 1, 2])
        model = LinearRegressionLearner()(data)
        self.assertAlmostEqual(float(model.intercept), -11)
        self.assertEqual(len(model.coefficients), 1)
        self.assertAlmostEqual(float(model.coefficients[0]), 1)

    def test_comparison_with_sklearn(self):
        alphas = [0.001, 0.1, 1, 10, 100]
        data = Table("housing")
        learners = [(LassoRegressionLearner, linear_model.Lasso),
                    (RidgeRegressionLearner, linear_model.Ridge)]
        for o_learner, s_learner in learners:
            for a in alphas:
                lr = o_learner(alpha=a)
                o_model = lr(data)
                s_model = s_learner(alpha=a, fit_intercept=True)
                s_model.fit(data.X, data.Y)
                delta = np.sum(s_model.coef_ - o_model.coefficients)
                self.assertAlmostEqual(delta, 0.0)
